from importlib import import_module

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def gpio_client_next() -> TestClient:
    app = FastAPI()
    gpio_router = import_module("openscan_firmware.routers.next.gpio")
    app.include_router(gpio_router.router, prefix="/next")
    with TestClient(app) as client:
        yield client


def test_next_gpio_patch_sets_pin_with_auto_init(monkeypatch, gpio_client_next):
    module_path = "openscan_firmware.routers.next.gpio"
    captured: dict[str, tuple[int, bool, bool]] = {}

    def fake_set_output_pin(pin: int, status: bool, auto_initialize: bool = False):
        captured["args"] = (pin, status, auto_initialize)
        return status

    monkeypatch.setattr(f"{module_path}.gpio.set_output_pin", fake_set_output_pin, raising=False)

    response = gpio_client_next.patch("/next/gpio/10", params={"status": "true"})

    assert response.status_code == 200
    assert response.json() is True
    assert captured["args"] == (10, True, True)


def test_next_gpio_patch_returns_clear_conflict_for_busy_pin(monkeypatch, gpio_client_next):
    module_path = "openscan_firmware.routers.next.gpio"
    detail = "Cannot set pin 10. Pin is initialized as button input."

    def fake_set_output_pin(pin: int, status: bool, auto_initialize: bool = False):
        raise ValueError(detail)

    monkeypatch.setattr(f"{module_path}.gpio.set_output_pin", fake_set_output_pin, raising=False)

    response = gpio_client_next.patch("/next/gpio/10", params={"status": "true"})

    assert response.status_code == 409
    assert response.json()["detail"] == detail
