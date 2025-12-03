import pytest
from fastapi.testclient import TestClient

from openscan.config.camera import CameraSettings
from openscan.config.motor import MotorConfig
from openscan.config.light import LightConfig
from openscan.main import app


class SettingsWrapper:
    """Simple wrapper that mimics the controller.settings contract."""

    def __init__(self, model):
        self.model = model

    def replace(self, new_model):
        self.model = new_model

    def update(self, **changes):
        self.model = self.model.model_copy(update=changes)


class DummyController:
    """Minimal controller mock providing a settings wrapper."""

    def __init__(self, model):
        self.settings = SettingsWrapper(model)


@pytest.fixture(name="client")
def fixture_client() -> TestClient:
    """Provide a TestClient for the FastAPI app."""
    with TestClient(app) as test_client:
        yield test_client


@pytest.mark.parametrize(
    (
        "module_path",
        "accessor_name",
        "resource_id",
        "url",
        "initial_factory",
        "replacement_factory",
        "patch_payload",
        "patch_field",
    ),
    [
        (
            "openscan.routers.v0_5.cameras",
            "get_camera_controller",
            "mock_cam",
            "/v0.5/cameras/mock_cam/settings",
            lambda: CameraSettings(),
            lambda: CameraSettings(shutter=120_000, AF=False),
            {"contrast": 1.5},
            "contrast",
        ),
        (
            "openscan.routers.v0_5.motors",
            "get_motor_controller",
            "mock_motor",
            "/v0.5/motors/mock_motor/settings",
            lambda: MotorConfig(
                direction_pin=1,
                enable_pin=2,
                step_pin=3,
                steps_per_rotation=3200,
            ),
            lambda: MotorConfig(
                direction_pin=1,
                enable_pin=2,
                step_pin=3,
                steps_per_rotation=3200,
                max_speed=6500,
            ),
            {"max_speed": 7000},
            "max_speed",
        ),
        (
            "openscan.routers.v0_5.lights",
            "get_light_controller",
            "mock_light",
            "/v0.5/lights/mock_light/settings",
            lambda: LightConfig(pins=[1]),
            lambda: LightConfig(pins=[1, 2], pwm=True),
            {"pwm": False},
            "pwm",
        ),
    ],
)
def test_v0_5_settings_endpoints_use_path_parameter(
    monkeypatch,
    client: TestClient,
    module_path: str,
    accessor_name: str,
    resource_id: str,
    url: str,
    initial_factory,
    replacement_factory,
    patch_payload: dict,
    patch_field: str,
) -> None:
    """Ensure v0.5 settings endpoints bind the resource name from the path."""
    initial_model = initial_factory()
    controller = DummyController(initial_model)

    def _stub(name: str):
        assert name == resource_id
        return controller

    monkeypatch.setattr(f"{module_path}.{accessor_name}", _stub)

    # GET returns the initial settings without requiring a query parameter.
    response = client.get(url)
    assert response.status_code == 200
    assert response.json() == controller.settings.model.model_dump()

    # PUT replaces the entire settings model and returns the updated representation.
    replacement_payload = replacement_factory().model_dump()
    replace_response = client.put(url, json=replacement_payload)
    assert replace_response.status_code == 200
    assert replace_response.json() == replacement_payload

    # PATCH updates individual fields on the current model using the path-bound name.
    patch_response = client.patch(url, json=patch_payload)
    assert patch_response.status_code == 200
    assert patch_response.json()[patch_field] == patch_payload[patch_field]
