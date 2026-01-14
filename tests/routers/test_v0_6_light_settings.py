import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openscan_firmware.config.light import LightConfig
from openscan_firmware.controllers.hardware import lights as lights_module
from openscan_firmware.routers.next.lights import router


class DummySettings:
    def __init__(self, model: LightConfig):
        self.model = model

    def replace(self, new_model: LightConfig) -> None:
        if isinstance(new_model, LightConfig):
            payload = new_model.model_dump()
        elif isinstance(new_model, dict):
            payload = new_model
        else:
            raise TypeError("Expected LightConfig")

        self.model = LightConfig.model_validate(payload)

    def update(self, **kwargs) -> None:
        payload = self.model.model_dump()
        payload.update(kwargs)
        self.model = LightConfig.model_validate(payload)


class DummyLightController:
    def __init__(self, initial_model: LightConfig):
        self.settings = DummySettings(initial_model)


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/v0.5")
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def fake_light_controller():
    controller = DummyLightController(LightConfig(pins=[1, 2], pwm_support=False))

    original_registry = lights_module._light_registry.copy()
    lights_module._light_registry.clear()
    lights_module._light_registry["ring"] = controller

    try:
        yield controller
    finally:
        lights_module._light_registry.clear()
        lights_module._light_registry.update(original_registry)


def test_get_light_settings_v05(client: TestClient, fake_light_controller: DummyLightController) -> None:
    response = client.get("/v0.5/lights/ring/settings")

    assert response.status_code == 200
    assert response.json()["pins"] == [1, 2]
    assert response.json()["pwm_support"] is False


def test_replace_light_settings_overwrites_configuration(
    client: TestClient, fake_light_controller: DummyLightController
) -> None:
    payload = {"pins": [3, 4], "pwm_support": True}

    response = client.put("/v0.5/lights/ring/settings", json=payload)

    assert response.status_code == 200
    assert fake_light_controller.settings.model.pins == [3, 4]
    assert fake_light_controller.settings.model.pwm_support is True
    assert response.json()["pins"] == [3, 4]
    assert response.json()["pwm_support"] is True


def test_replace_light_settings_returns_422_on_controller_error(
    client: TestClient,
    fake_light_controller: DummyLightController,
    monkeypatch,
) -> None:
    def _failing_replace(new_settings):  # pragma: no cover - exercised via endpoint
        raise ValueError("invalid replace")

    monkeypatch.setattr(fake_light_controller.settings, "replace", _failing_replace)

    response = client.put("/v0.5/lights/ring/settings", json={"pins": [5], "pwm_support": False})

    assert response.status_code == 422
    assert response.json()["detail"] == "invalid replace"


def test_update_light_settings_applies_partial_changes(
    client: TestClient, fake_light_controller: DummyLightController
) -> None:
    response = client.patch("/v0.5/lights/ring/settings", json={"pwm_support": True})

    assert response.status_code == 200
    assert fake_light_controller.settings.model.pins == [1, 2]
    assert fake_light_controller.settings.model.pwm_support is True
    assert response.json()["pwm_support"] is True


def test_update_light_settings_returns_422_on_controller_error(
    client: TestClient,
    fake_light_controller: DummyLightController,
    monkeypatch,
) -> None:
    def _failing_update(**kwargs):  # pragma: no cover - exercised via endpoint
        raise ValueError("invalid update")

    monkeypatch.setattr(fake_light_controller.settings, "update", _failing_update)

    response = client.patch("/v0.5/lights/ring/settings", json={"pwm_support": True})

    assert response.status_code == 422
    assert response.json()["detail"] == "invalid update"
