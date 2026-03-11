"""Integration-style tests for the device router endpoints."""

from __future__ import annotations

import json
from typing import Callable

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def device_client(latest_router_loader) -> TestClient:
    """Provide a FastAPI client with the latest device router mounted."""

    app = FastAPI()
    device_router = latest_router_loader("device")
    app.include_router(device_router.router, prefix="/latest")
    with TestClient(app) as client:
        yield client


@pytest.fixture
def device_router_path(latest_router_path) -> Callable[[str], str]:  # type: ignore[override]
    """Shortcut to build module paths for the latest router version."""

    return latest_router_path


def test_set_config_file_returns_factory_defaults(monkeypatch, tmp_path, device_client, device_router_path):
    module_path = device_router_path("device")

    preset_path = tmp_path / "mini.json"
    preset_path.write_text(json.dumps({
        "name": "Preset",
        "model": "mini",
        "shield": "greenshield",
        "cameras": {},
        "motors": {},
        "lights": {},
        "endstops": {},
    }))

    monkeypatch.setattr(
        f"{module_path}.device.get_available_configs",
        lambda: [
            {
                "filename": "mini.json",
                "path": str(preset_path),
            }
        ],
        raising=False,
    )

    captured = {}

    async def fake_set_device_config(path: str):
        captured["path"] = path
        return True

    monkeypatch.setattr(f"{module_path}.device.set_device_config", fake_set_device_config, raising=False)

    status_payload = {
        "name": "Preset",
        "model": "mini",
        "shield": "greenshield",
        "cameras": {},
        "motors": {},
        "lights": {},
        "motors_timeout": 0.0,
        "startup_mode": "startup_enabled",
        "calibrate_mode": "calibrate_manual",
        "initialized": False,
    }
    monkeypatch.setattr(f"{module_path}.device.get_device_info", lambda: status_payload, raising=False)

    response = device_client.put(
        "/latest/device/configurations/current",
        json={"config_file": "mini.json"},
    )

    assert response.status_code == 200
    assert captured["path"] == str(preset_path)

    payload = response.json()
    assert payload["success"] is True
    assert payload["status"]["motors_timeout"] == 0.0
    assert payload["status"]["startup_mode"] == "startup_enabled"
    assert payload["status"]["calibrate_mode"] == "calibrate_manual"


def test_reinitialize_endpoint_calls_controller(monkeypatch, device_client, device_router_path):
    module_path = device_router_path("device")

    motor_settings = {
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
    }
    light_settings = {
        "pins": [17, 27],
        "pwm_support": False,
    }

    camera_settings = {
        "shutter": 50.0,
        "orientation_flag": 1,
    }

    status_payload = {
        "name": "Preset",
        "model": "mini",
        "shield": "greenshield",
        "cameras": {
            "cam": {
                "name": "cam",
                "type": "linuxpy",
                "busy": False,
                "settings": camera_settings,
            }
        },
        "motors": {
            "rotor": {
                "name": "rotor",
                "angle": 0.0,
                "busy": False,
                "target_angle": None,
                "settings": motor_settings,
                "endstop": None,
            }
        },
        "lights": {
            "ring": {
                "name": "ring",
                "is_on": False,
                "settings": light_settings,
            }
        },
        "motors_timeout": 0.0,
        "startup_mode": "startup_enabled",
        "calibrate_mode": "calibrate_manual",
        "initialized": True,
    }
    monkeypatch.setattr(f"{module_path}.device.get_device_info", lambda: status_payload, raising=False)

    detected_args: list[bool] = []

    async def fake_initialize(*, detect_cameras: bool = False):
        detected_args.append(detect_cameras)

    monkeypatch.setattr(f"{module_path}.device.initialize", fake_initialize, raising=False)
    response = device_client.post(
        "/latest/device/configurations/current/initialize",
        params={"detect_cameras": "true"},
    )

    assert response.status_code == 200
    assert detected_args == [True]
    payload = response.json()
    assert payload["success"] is True
    assert payload["status"]["initialized"] is True
    assert set(payload["status"]["motors"].keys()) == {"rotor"}
    assert set(payload["status"]["lights"].keys()) == {"ring"}
