import pytest
from unittest.mock import MagicMock
from datetime import datetime

from app.config.camera import CameraSettings
from app.config.scan import ScanSetting
from app.models.paths import PathMethod
from app.models.scan import Scan


@pytest.fixture
def mock_camera_controller() -> MagicMock:
    """Fixture for a mocked CameraController."""
    controller = MagicMock()
    controller.settings = MagicMock()
    controller.settings.model = CameraSettings(shutter=400)
    controller.model.name = "mock_camera"
    return controller


@pytest.fixture
def sample_scan_settings() -> ScanSetting:
    """
    Returns a sample ScanSetting object for testing.
    """
    return ScanSetting(
        path_method=PathMethod.FIBONACCI,
        points=10,
        min_theta=0.0,
        max_theta=170.0,
        optimize_path=True,
        optimization_algorithm="nearest_neighbor",
        focus_stacks=1,
        focus_range=(10.0, 15.0)
    )


@pytest.fixture(params=[
    {
        "path_method": "fibonacci",
        "points": 10,
        "min_theta": 0.0,
        "max_theta": 170.0,
        "optimize_path": True,
        "optimization_algorithm": "nearest_neighbor",
        "focus_stacks": 1,
        "focus_range": [10.0, 15.0]
    },
    {
        "path_method": "fibonacci",
        "points": 25,
        "min_theta": 10.0,
        "max_theta": 160.0,
        "optimize_path": False,
        "focus_stacks": 3,
        "focus_range": [5.0, 15.0]
    }
])
def valid_scan_settings_payload(request):
    """
    Provides parametrized, JSON-serializable scan settings payloads for API tests.
    """
    return request.param


@pytest.fixture
def sample_scan_model(sample_scan_settings: ScanSetting) -> Scan:
    """Provides a sample Scan model instance."""
    return Scan(
        project_name="test_project",
        index=1,
        created=datetime.now(),
        description="Test Scan",
        settings=sample_scan_settings,
        camera_settings=CameraSettings(),
    )
