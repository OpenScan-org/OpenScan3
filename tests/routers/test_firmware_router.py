"""Integration-style tests for the firmware router endpoints."""

from __future__ import annotations

from importlib import import_module

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _next_router_module_path(name: str) -> str:
    return f"openscan_firmware.routers.next.{name}"


@pytest.fixture
def firmware_client() -> TestClient:
    """Provide a FastAPI client with the next firmware router mounted."""

    app = FastAPI()
    firmware_router = import_module(_next_router_module_path("firmware"))
    app.include_router(firmware_router.router, prefix="/latest")
    with TestClient(app) as client:
        yield client


def test_get_firmware_settings_returns_current_settings(monkeypatch, firmware_client):
    module_path = _next_router_module_path("firmware")
    router_module = import_module(module_path)

    monkeypatch.setattr(
        f"{module_path}.get_firmware_settings",
        lambda: router_module.FirmwareSettings(qr_wifi_scan_enabled=True),
    )

    response = firmware_client.get("/latest/firmware/settings")

    assert response.status_code == 200
    assert response.json() == {
        "qr_wifi_scan_enabled": True,
        "enable_cloud": False,
    }


def test_put_firmware_settings_replaces_payload(monkeypatch, firmware_client):
    module_path = _next_router_module_path("firmware")
    captured: dict[str, bool] = {}

    def fake_save(settings):
        captured["qr_wifi_scan_enabled"] = settings.qr_wifi_scan_enabled
        captured["enable_cloud"] = settings.enable_cloud

    monkeypatch.setattr(f"{module_path}.save_firmware_settings", fake_save)

    response = firmware_client.put(
        "/latest/firmware/settings",
        json={"qr_wifi_scan_enabled": False, "enable_cloud": True},
    )

    assert response.status_code == 200
    assert response.json() == {
        "qr_wifi_scan_enabled": False,
        "enable_cloud": True,
    }
    assert captured["qr_wifi_scan_enabled"] is False
    assert captured["enable_cloud"] is True


def test_patch_firmware_setting_updates_single_key(monkeypatch, firmware_client):
    module_path = _next_router_module_path("firmware")
    router_module = import_module(module_path)

    monkeypatch.setattr(
        f"{module_path}.get_firmware_settings",
        lambda: router_module.FirmwareSettings(qr_wifi_scan_enabled=True),
    )

    saved: dict[str, bool] = {}

    def fake_save(settings):
        saved["qr_wifi_scan_enabled"] = settings.qr_wifi_scan_enabled

    monkeypatch.setattr(f"{module_path}.save_firmware_settings", fake_save)

    response = firmware_client.patch(
        "/latest/firmware/settings/qr_wifi_scan_enabled",
        json={"value": False},
    )

    assert response.status_code == 200
    assert response.json() == {
        "qr_wifi_scan_enabled": False,
        "enable_cloud": False,
    }
    assert saved["qr_wifi_scan_enabled"] is False


def test_patch_firmware_setting_unknown_key_returns_404(monkeypatch, firmware_client):
    module_path = _next_router_module_path("firmware")
    router_module = import_module(module_path)

    monkeypatch.setattr(
        f"{module_path}.get_firmware_settings",
        lambda: router_module.FirmwareSettings(qr_wifi_scan_enabled=True),
    )

    response = firmware_client.patch(
        "/latest/firmware/settings/not_a_real_key",
        json={"value": False},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Unknown firmware setting key: not_a_real_key"
