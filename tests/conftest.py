import pytest
from unittest.mock import MagicMock
from datetime import datetime
from pathlib import Path
import io

from app.controllers.services.projects import ProjectManager
from app.config.camera import CameraSettings
from app.config.scan import ScanSetting
from app.models.paths import PathMethod
from app.models.scan import Scan
from app.models.camera import CameraMetadata, PhotoData


@pytest.fixture
def MOCKED_PROJECTS_PATH(tmp_path) -> Path:
    """Fixture to create a temporary, isolated projects directory for testing."""
    mock_projects_dir = tmp_path / "projects_test_root"
    mock_projects_dir.mkdir()
    return mock_projects_dir


@pytest.fixture
def project_manager(MOCKED_PROJECTS_PATH) -> ProjectManager:
    """Fixture to create a ProjectManager instance with a temporary projects path."""
    # Instantiate the ProjectManager. Assuming __init__ is synchronous and loads data.
    # If ProjectManager needs async initialization, this fixture would need to be async
    # and use an async factory or `await ProjectManager.create(...)` pattern.
    pm = ProjectManager(path=MOCKED_PROJECTS_PATH)
    return pm


@pytest.fixture
def mock_camera_controller() -> MagicMock:
    """Fixture for a mocked CameraController."""
    controller = MagicMock()
    controller.settings = MagicMock()
    controller.settings.model = MagicMock()
    controller.settings.model = CameraSettings(shutter=400)
    controller.camera.name = "mock_camera"
    return controller

@pytest.fixture
def sample_camera_metadata() -> CameraMetadata:
    """Fixture for a mocked CameraMetadata."""
    return CameraMetadata(camera_name="mock_camera",
                          camera_settings=CameraSettings(shutter=400),
                          raw_metadata={})

@pytest.fixture
def fake_photo_data(sample_camera_metadata: CameraMetadata) -> PhotoData:
    """Fixture for a mocked PhotoData."""
    return PhotoData(data=io.BytesIO(b"fake_image_bytes"),
                     format="jpeg",
                     camera_metadata=sample_camera_metadata)

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
        focus_range=(10.0, 15.0),
        image_format="jpeg"
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
