"""Baseline integration-style tests for the v0_8 device router contract."""

from __future__ import annotations

import json
from importlib import import_module
from pathlib import Path
from typing import Callable

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _v08_router_module_path(name: str) -> str:
    return f"openscan_firmware.routers.v0_8.{name}"


@pytest.fixture
def device_client_v08() -> TestClient:
    """Provide a FastAPI client with the v0_8 device router mounted."""

    app = FastAPI()
    device_router = import_module(_v08_router_module_path("device"))
    app.include_router(device_router.router, prefix="/v0.8")
    with TestClient(app) as client:
        yield client


@pytest.fixture
def device_router_path_v08() -> Callable[[str], str]:
    """Shortcut to build module paths for the v0_8 router version."""

    return _v08_router_module_path


def test_v08_set_config_file_uses_available_config(monkeypatch, tmp_path, device_client_v08, device_router_path_v08):
    module_path = device_router_path_v08("device")

    preset_path = tmp_path / "mini.json"
    preset_path.write_text("{}")

    monkeypatch.setattr(
        f"{module_path}.device.get_available_configs",
        lambda: [{"filename": "mini.json", "path": str(preset_path)}],
        raising=False,
    )

    captured: dict[str, str] = {}

    async def fake_set_device_config(path: str):
        captured["path"] = path
        return True

    monkeypatch.setattr(f"{module_path}.device.set_device_config", fake_set_device_config, raising=False)
    monkeypatch.setattr(
        f"{module_path}.device.get_device_info",
        lambda: {
            "name": "Preset",
            "model": "mini",
            "shield": "greenshield",
            "cameras": {},
            "motors": {},
            "lights": {},
            "motors_timeout": 0.0,
            "startup_mode": "startup_enabled",
            "calibrate_mode": "calibrate_manual",
            "initialized": True,
        },
        raising=False,
    )

    class _PassthroughStatus:
        @staticmethod
        def model_validate(payload):
            return payload

    monkeypatch.setattr(f"{module_path}.DeviceStatusResponse", _PassthroughStatus, raising=False)

    response = device_client_v08.put(
        "/v0.8/device/configurations/current",
        json={"config_file": "mini.json"},
    )

    assert response.status_code == 200
    assert captured["path"] == str(preset_path)

    payload = response.json()
    assert payload["success"] is True
    assert payload["message"] == "Configuration loaded successfully"
    assert payload["status"]["initialized"] is True


def test_v08_reinitialize_endpoint_calls_controller(monkeypatch, device_client_v08, device_router_path_v08):
    module_path = device_router_path_v08("device")

    monkeypatch.setattr(
        f"{module_path}.device.get_device_info",
        lambda: {
            "name": "Preset",
            "model": "mini",
            "shield": "greenshield",
            "cameras": {},
            "motors": {},
            "lights": {},
            "motors_timeout": 0.0,
            "startup_mode": "startup_enabled",
            "calibrate_mode": "calibrate_manual",
            "initialized": True,
        },
        raising=False,
    )

    class _PassthroughStatus:
        @staticmethod
        def model_validate(payload):
            return payload

    monkeypatch.setattr(f"{module_path}.DeviceStatusResponse", _PassthroughStatus, raising=False)

    detected_args: list[bool] = []

    async def fake_initialize(*, detect_cameras: bool = False):
        detected_args.append(detect_cameras)

    monkeypatch.setattr(f"{module_path}.device.initialize", fake_initialize, raising=False)

    response = device_client_v08.post(
        "/v0.8/device/configurations/current/initialize",
        params={"detect_cameras": "true"},
    )

    assert response.status_code == 200
    assert detected_args == [True]

    payload = response.json()
    assert payload["success"] is True
    assert payload["message"] == "Hardware reinitialized successfully"


def test_v08_add_config_json_rejects_persisted_shape(device_client_v08):
    repo_root = Path(__file__).resolve().parents[2]
    default_config = repo_root / "settings" / "device" / "default_mini_greenshield.json"
    assert default_config.exists(), "Expected default config file to exist"

    persisted_payload = default_config.read_text()

    response = device_client_v08.post(
        "/v0.8/device/configurations/",
        json={
            "config_data": json.loads(persisted_payload),
            "filename": {"config_file": "legacy_strict_contract"},
        },
    )

    assert response.status_code == 422


def test_v08_add_config_json_translates_legacy_shape(monkeypatch, tmp_path, device_client_v08, device_router_path_v08):
    module_path = device_router_path_v08("device")

    settings_root = tmp_path
    (settings_root / "device").mkdir()
    monkeypatch.setenv("OPENSCAN_SETTINGS_DIR", str(settings_root))

    monkeypatch.setattr(
        f"{module_path}.device.get_device_info",
        lambda: {
            "name": "Preset",
            "model": "mini",
            "shield": "greenshield",
            "cameras": {},
            "motors": {},
            "lights": {},
            "motors_timeout": 0.0,
            "startup_mode": "startup_enabled",
            "calibrate_mode": "calibrate_manual",
            "initialized": True,
        },
        raising=False,
    )

    class _PassthroughStatus:
        @staticmethod
        def model_validate(payload):
            return payload

    monkeypatch.setattr(f"{module_path}.DeviceStatusResponse", _PassthroughStatus, raising=False)

    response = device_client_v08.post(
        "/v0.8/device/configurations/",
        json={
            "config_data": {
                "name": "LegacyPayload",
                "model": "mini",
                "shield": "greenshield",
                "cameras": {},
                "motors": {
                    "rotor": {
                        "name": "rotor",
                        "settings": {
                            "direction_pin": 5,
                            "enable_pin": 23,
                            "step_pin": 6,
                            "acceleration": 20000,
                            "max_speed": 5000,
                            "direction": 1,
                            "steps_per_rotation": 42667,
                            "min_angle": 0,
                            "max_angle": 360,
                            "home_angle": 90,
                        },
                        "angle": 90.0,
                    }
                },
                "lights": {
                    "ring": {
                        "name": "ring",
                        "settings": {
                            "pins": [17, 27],
                            "pwm_support": False,
                        },
                    }
                },
                "endstops": {},
                "motors_timeout": 1.5,
                "startup_mode": "startup_enabled",
                "calibrate_mode": "calibrate_manual",
            },
            "filename": {"config_file": "legacy_adapter_out"},
        },
    )

    assert response.status_code == 200

    written_file = settings_root / "device" / "legacy_adapter_out.json"
    assert written_file.exists()

    written_payload = json.loads(written_file.read_text())
    assert written_payload["name"] == "LegacyPayload"
    assert written_payload["motors"]["rotor"]["direction_pin"] == 5
    assert "name" not in written_payload["motors"]["rotor"]
    assert written_payload["lights"]["ring"]["pins"] == [17, 27]
    assert "name" not in written_payload["lights"]["ring"]


def test_v08_get_current_config_is_not_available(device_client_v08):
    response = device_client_v08.get("/v0.8/device/configurations/current")
    assert response.status_code == 405
