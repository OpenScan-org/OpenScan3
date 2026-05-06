"""Baseline integration-style tests for the v0_9 device router contract."""

from __future__ import annotations

from importlib import import_module
from typing import Callable

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _v09_router_module_path(name: str) -> str:
    return f"openscan_firmware.routers.v0_9.{name}"


@pytest.fixture
def device_client_v09() -> TestClient:
    app = FastAPI()
    device_router = import_module(_v09_router_module_path("device"))
    app.include_router(device_router.router, prefix="/v0.9")
    with TestClient(app) as client:
        yield client


@pytest.fixture
def device_router_path_v09() -> Callable[[str], str]:
    return _v09_router_module_path


def test_v09_wakeup_endpoint_resumes_idle_device(monkeypatch, device_client_v09, device_router_path_v09):
    module_path = device_router_path_v09("device")

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
    monkeypatch.setattr(f"{module_path}.device.is_idle", lambda: True, raising=False)

    wake_calls = {"resume": 0}

    async def fake_resume():
        wake_calls["resume"] += 1

    async def fake_recalibrate():
        raise AssertionError("recalibrate_motors should not be called in this scenario")

    monkeypatch.setattr(f"{module_path}.device.resume_from_idle", fake_resume, raising=False)
    monkeypatch.setattr(f"{module_path}.device.recalibrate_motors", fake_recalibrate, raising=False)

    class _ScannerDevice:
        calibrate_mode = "calibrate_manual"

    monkeypatch.setattr(f"{module_path}.device._scanner_device", _ScannerDevice(), raising=False)

    response = device_client_v09.post("/v0.9/device/wakeup")
    assert response.status_code == 200

    payload = response.json()
    assert payload["success"] is True
    assert payload["message"] == "Device awakened successfully"
    assert wake_calls["resume"] == 1
