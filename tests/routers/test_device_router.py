"""Integration-style tests for the device router endpoints."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from importlib import import_module
from typing import Callable

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _next_router_module_path(name: str) -> str:
    return f"openscan_firmware.routers.next.{name}"


@pytest.fixture
def device_client() -> TestClient:
    """Provide a FastAPI client with the next device router mounted."""

    app = FastAPI()
    device_router = import_module(_next_router_module_path("device"))
    app.include_router(device_router.router, prefix="/latest")
    with TestClient(app) as client:
        yield client


@pytest.fixture
def device_router_path() -> Callable[[str], str]:
    """Shortcut to build module paths for the next router version."""

    return _next_router_module_path


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

    class _PassthroughStatus:
        @staticmethod
        def model_validate(payload):
            return payload

    monkeypatch.setattr(f"{module_path}.DeviceStatusResponse", _PassthroughStatus, raising=False)

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


def test_get_current_config_returns_payload(monkeypatch, tmp_path, device_client, device_router_path):
    module_path = device_router_path("device")

    config_payload = {
        "name": "UnitConfig",
        "model": "mini",
        "shield": "greenshield",
        "cameras": {},
        "motors": {},
        "lights": {},
        "endstops": None,
        "motors_timeout": 3.5,
        "startup_mode": "startup_enabled",
        "calibrate_mode": "calibrate_manual",
    }

    config_path = tmp_path / "device_config.json"
    monkeypatch.setattr(f"{module_path}.device.DEVICE_CONFIG_FILE", config_path, raising=False)
    monkeypatch.setattr(f"{module_path}.device.load_device_config", lambda: config_payload.copy(), raising=False)

    response = device_client.get("/latest/device/configurations/current")
    assert response.status_code == 200

    payload = response.json()
    assert payload["filename"] == "device_config.json"
    assert payload["config"] == config_payload


def test_get_named_config_reads_disk(monkeypatch, tmp_path, device_client, device_router_path):
    module_path = device_router_path("device")

    settings_root = tmp_path
    device_dir = settings_root / "device"
    device_dir.mkdir()

    config_payload = {
        "name": "NamedConfig",
        "model": "mini",
        "shield": "greenshield",
        "cameras": {},
        "motors": {},
        "lights": {},
        "endstops": None,
        "motors_timeout": 1.0,
        "startup_mode": "startup_enabled",
        "calibrate_mode": "calibrate_manual",
    }

    target_file = device_dir / "custom.json"
    target_file.write_text(json.dumps(config_payload))

    monkeypatch.setenv("OPENSCAN_SETTINGS_DIR", str(settings_root))

    response = device_client.get("/latest/device/configurations/custom")
    assert response.status_code == 200

    payload = response.json()
    assert payload["filename"] == "custom.json"
    assert payload["config"] == config_payload


def test_config_roundtrip_flow(monkeypatch, tmp_path, device_client, device_router_path):
    module_path = device_router_path("device")

    repo_root = Path(__file__).resolve().parents[2]
    default_config = repo_root / "settings" / "device" / "default_mini_greenshield.json"
    assert default_config.exists(), "Expected default config file to exist"

    settings_root = tmp_path
    device_dir = settings_root / "device"
    device_dir.mkdir()
    monkeypatch.setenv("OPENSCAN_SETTINGS_DIR", str(settings_root))

    shutil.copy(default_config, device_dir / default_config.name)

    device_config_path = device_dir / "device_config.json"
    monkeypatch.setattr(f"{module_path}.device.DEVICE_CONFIG_FILE", device_config_path, raising=False)

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
        "initialized": True,
    }
    monkeypatch.setattr(f"{module_path}.device.get_device_info", lambda: status_payload, raising=False)
    monkeypatch.setattr(f"{module_path}.device.save_device_config", lambda: True, raising=False)

    captured: dict[str, dict] = {}

    async def fake_initialize(config: dict, detect_cameras: bool = False):
        captured["config"] = config

    monkeypatch.setattr(f"{module_path}.device.initialize", fake_initialize, raising=False)

    get_response = device_client.get("/latest/device/configurations/default_mini_greenshield")
    assert get_response.status_code == 200

    config_payload = get_response.json()["config"]
    config_payload["name"] = "IntegrationTest"
    config_payload["motors_timeout"] = 12.5

    new_file = device_dir / "integration_override.json"
    new_file.write_text(json.dumps(config_payload))

    put_response = device_client.put(
        "/latest/device/configurations/current",
        json={"config_file": "integration_override.json"},
    )
    assert put_response.status_code == 200
    assert captured["config"]["name"] == "IntegrationTest"
    assert captured["config"]["motors_timeout"] == 12.5

    payload = put_response.json()
    assert payload["success"] is True
    assert payload["status"]["initialized"] is True


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
                "calibrated": True,
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
