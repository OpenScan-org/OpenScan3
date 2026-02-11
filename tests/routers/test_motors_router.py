"""Tests for the next motors router angle override endpoint."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from openscan_firmware.config.motor import MotorConfig
from openscan_firmware.main import app
import openscan_firmware.routers.next.motors as motors_module


def _make_motor_config() -> MotorConfig:
    return MotorConfig(
        direction_pin=1,
        enable_pin=2,
        step_pin=3,
        acceleration=20000,
        max_speed=5000,
        direction=1,
        steps_per_rotation=3200,
        min_angle=0,
        max_angle=360,
    )


class DummyMotorController:
    """Lightweight stand-in mimicking the MotorController interface."""

    def __init__(self, name: str = "rotor", angle: float = 0.0, busy: bool = False):
        self.name = name
        self.model = SimpleNamespace(angle=angle)
        self._busy = busy
        self._config = _make_motor_config()

    def is_busy(self) -> bool:
        return self._busy

    def set_busy(self, busy: bool) -> None:
        self._busy = busy

    def get_status(self) -> dict:
        return {
            "name": self.name,
            "angle": self.model.angle,
            "busy": self._busy,
            "target_angle": None,
            "settings": self._config,
            "endstop": None,
        }


class DummyEndstopController(DummyMotorController):
    """Dummy controller enriched with endstop + async move helpers."""

    def __init__(self, *args, has_endstop: bool = True, **kwargs):
        super().__init__(*args, **kwargs)
        self.endstop = object() if has_endstop else None
        self.move_degrees = AsyncMock()
        self.move_to = AsyncMock()


@pytest.fixture(name="client")
def fixture_client() -> TestClient:
    """Provide a FastAPI test client."""

    with TestClient(app) as test_client:
        yield test_client


# -----------------
# Unit tests
# -----------------


@pytest.mark.asyncio
async def test_override_motor_angle_updates_model(monkeypatch: pytest.MonkeyPatch):
    controller = DummyMotorController(angle=10.0)
    monkeypatch.setattr(
        motors_module,
        "get_motor_controller",
        lambda name: controller,
        raising=False,
    )

    response = await motors_module.override_motor_angle("rotor", angle=123.4)

    assert controller.model.angle == pytest.approx(123.4)
    assert response["angle"] == pytest.approx(123.4)


@pytest.mark.asyncio
async def test_override_motor_angle_uses_default_value(monkeypatch: pytest.MonkeyPatch):
    controller = DummyMotorController(angle=10.0)
    monkeypatch.setattr(
        motors_module,
        "get_motor_controller",
        lambda name: controller,
        raising=False,
    )

    default_param = motors_module.override_motor_angle.__defaults__[0]
    default_value = getattr(default_param, "default", default_param)

    response = await motors_module.override_motor_angle("rotor", angle=default_value)

    assert default_value == pytest.approx(90.0)
    assert controller.model.angle == pytest.approx(default_value)
    assert response["angle"] == pytest.approx(default_value)


def test_get_motor_controller_or_404_raises_http_exception(monkeypatch: pytest.MonkeyPatch):
    def _missing(_name: str):
        raise ValueError("Controller not found: rotor")

    monkeypatch.setattr(
        motors_module,
        "get_motor_controller",
        _missing,
        raising=False,
    )

    with pytest.raises(HTTPException) as exc:
        motors_module._get_motor_controller_or_404("rotor")

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_override_motor_angle_busy(monkeypatch: pytest.MonkeyPatch):
    controller = DummyMotorController(angle=10.0, busy=True)
    monkeypatch.setattr(
        motors_module,
        "get_motor_controller",
        lambda name: controller,
        raising=False,
    )

    with pytest.raises(HTTPException) as exc:
        await motors_module.override_motor_angle("rotor", angle=25.0)

    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_override_motor_angle_missing_motor(monkeypatch: pytest.MonkeyPatch):
    def _missing(_name: str):
        raise ValueError("Controller not found: rotor")

    monkeypatch.setattr(
        motors_module,
        "get_motor_controller",
        _missing,
        raising=False,
    )

    with pytest.raises(HTTPException) as exc:
        await motors_module.override_motor_angle("rotor")

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_move_motor_to_home_position_success(monkeypatch: pytest.MonkeyPatch):
    controller = DummyEndstopController(angle=45.0)

    monkeypatch.setattr(
        motors_module,
        "get_motor_controller",
        lambda name: controller,
        raising=False,
    )

    fake_sleep = AsyncMock()
    monkeypatch.setattr(motors_module.asyncio, "sleep", fake_sleep)

    response = await motors_module.move_motor_to_home_position("rotor")

    assert controller.model.angle == 0
    controller.move_degrees.assert_awaited_once_with(140)
    controller.move_to.assert_awaited_once_with(90)
    fake_sleep.assert_awaited_once()
    assert response["name"] == "rotor"


@pytest.mark.asyncio
@pytest.mark.parametrize("has_endstop,is_busy", ((False, False), (True, True)))
async def test_move_motor_to_home_position_invalid(monkeypatch: pytest.MonkeyPatch, has_endstop: bool, is_busy: bool):
    controller = DummyEndstopController(angle=45.0, has_endstop=has_endstop)
    controller.set_busy(is_busy)

    monkeypatch.setattr(
        motors_module,
        "get_motor_controller",
        lambda name: controller,
        raising=False,
    )

    with pytest.raises(HTTPException) as exc:
        await motors_module.move_motor_to_home_position("rotor")

    assert exc.value.status_code == 422
    assert controller.move_degrees.await_count == 0
    assert controller.move_to.await_count == 0


# -----------------
# Integration tests
# -----------------


def _patch_controller(monkeypatch: pytest.MonkeyPatch, controller: DummyMotorController, valid_name: str = "rotor") -> None:
    def _get(name: str):
        if name != valid_name:
            raise ValueError(f"Controller not found: {name}")
        return controller

    monkeypatch.setattr(
        motors_module,
        "get_motor_controller",
        _get,
        raising=False,
    )


def test_angle_override_endpoint_success(monkeypatch: pytest.MonkeyPatch, client: TestClient):
    controller = DummyMotorController(angle=0.0)
    _patch_controller(monkeypatch, controller)

    response = client.put("/next/motors/rotor/angle-override?angle=135.0")

    assert response.status_code == 200
    assert controller.model.angle == pytest.approx(135.0)
    assert response.json()["angle"] == pytest.approx(135.0)


def test_angle_override_endpoint_busy_conflict(monkeypatch: pytest.MonkeyPatch, client: TestClient):
    controller = DummyMotorController(angle=0.0, busy=True)
    _patch_controller(monkeypatch, controller)

    response = client.put("/next/motors/rotor/angle-override?angle=15.0")

    assert response.status_code == 409
    assert response.json()["detail"].startswith("Motor is currently moving")


def test_angle_override_endpoint_recovers_after_conflict(monkeypatch: pytest.MonkeyPatch, client: TestClient):
    controller = DummyMotorController(angle=0.0, busy=True)
    _patch_controller(monkeypatch, controller)

    first = client.put("/next/motors/rotor/angle-override?angle=15.0")
    assert first.status_code == 409

    controller.set_busy(False)
    second = client.put("/next/motors/rotor/angle-override?angle=75.0")
    assert second.status_code == 200
    assert controller.model.angle == pytest.approx(75.0)


def test_angle_override_endpoint_default_angle(monkeypatch: pytest.MonkeyPatch, client: TestClient):
    controller = DummyMotorController(angle=0.0)
    _patch_controller(monkeypatch, controller)

    response = client.put("/next/motors/rotor/angle-override")

    assert response.status_code == 200
    assert controller.model.angle == pytest.approx(90.0)


def test_angle_override_endpoint_unknown_motor(monkeypatch: pytest.MonkeyPatch, client: TestClient):
    def _missing(_name: str):
        raise ValueError("Controller not found: rotor")

    monkeypatch.setattr(
        motors_module,
        "get_motor_controller",
        _missing,
        raising=False,
    )

    response = client.put("/next/motors/rotor/angle-override?angle=15.0")

    assert response.status_code == 404
    assert response.json()["detail"].startswith("Controller not found")


def test_move_motor_to_angle_unknown_motor(monkeypatch: pytest.MonkeyPatch, client: TestClient):
    def _missing(_name: str):
        raise ValueError("Controller not found: rotor")

    monkeypatch.setattr(
        motors_module,
        "get_motor_controller",
        _missing,
        raising=False,
    )

    response = client.put("/next/motors/rotor/angle?degrees=45")

    assert response.status_code == 404


def test_move_motor_by_degree_unknown_motor(monkeypatch: pytest.MonkeyPatch, client: TestClient):
    def _missing(_name: str):
        raise ValueError("Controller not found: rotor")

    monkeypatch.setattr(
        motors_module,
        "get_motor_controller",
        _missing,
        raising=False,
    )

    response = client.patch("/next/motors/rotor/angle", json={"degrees": 10})

    assert response.status_code == 404


def test_endstop_calibration_endpoint_success(monkeypatch: pytest.MonkeyPatch, client: TestClient):
    controller = DummyEndstopController(angle=10.0)
    _patch_controller(monkeypatch, controller)

    fake_sleep = AsyncMock()
    monkeypatch.setattr(motors_module.asyncio, "sleep", fake_sleep)

    response = client.put("/next/motors/rotor/endstop-calibration")

    assert response.status_code == 200
    controller.move_degrees.assert_awaited_once_with(140)
    controller.move_to.assert_awaited_once_with(90)


def test_endstop_calibration_endpoint_no_endstop(monkeypatch: pytest.MonkeyPatch, client: TestClient):
    controller = DummyEndstopController(angle=10.0, has_endstop=False)
    _patch_controller(monkeypatch, controller)

    response = client.put("/next/motors/rotor/endstop-calibration")

    assert response.status_code == 422
