import asyncio
import os
import shutil
from importlib import import_module

import pytest
import pytest_asyncio
from unittest.mock import MagicMock
from datetime import datetime
from pathlib import Path
import io
from PIL import Image

from openscan_firmware.controllers.services.projects import ProjectManager, save_project
from openscan_firmware.controllers.services.tasks.task_manager import TaskManager, TASKS_STORAGE_PATH
from openscan_firmware.models.task import TaskStatus
from openscan_firmware.config.camera import CameraSettings
from openscan_firmware.config.scan import ScanSetting
from openscan_firmware.models.paths import PathMethod
from openscan_firmware.models.scan import Scan
from openscan_firmware.models.camera import CameraMetadata, PhotoData
from openscan_firmware.models.motor import Motor
from openscan_firmware.config.motor import MotorConfig
from openscan_firmware.models.light import Light
from openscan_firmware.config.light import LightConfig
from openscan_firmware.main import LATEST


def _latest_router_module_path(name: str) -> str:
    version_folder = f"v{LATEST.replace('.', '_')}"
    return f"openscan_firmware.routers.{version_folder}.{name}"


def _import_latest_router_module(name: str):
    return import_module(_latest_router_module_path(name))


@pytest.fixture
def latest_router_loader():
    return _import_latest_router_module


@pytest.fixture
def latest_router_path():
    return _latest_router_module_path

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
def motor_config_instance():
    """Provides a MotorConfig instance for tests."""
    return MotorConfig(
        direction_pin=1, enable_pin=2, step_pin=3,
        acceleration=20000, max_speed=7500,
        min_angle=0, max_angle=360,
        direction=1, steps_per_rotation=3200
    )

@pytest.fixture
def motor_model_instance(motor_config_instance):
    """Provides a Motor model instance, initialized at angle 0."""
    return Motor(name="test_motor", settings=motor_config_instance, angle=90.0)

@pytest.fixture
def light_model_instance():
    """Provides a Light model instance."""
    return Light(name="test_light", settings=LightConfig(pins=[1, 2]))


@pytest.fixture(scope="function")
def task_manager_storage_path(tmp_path_factory):
    """Provide an isolated persistence directory for TaskManager tests."""

    temp_storage = tmp_path_factory.mktemp("task_manager_storage")
    yield temp_storage
    shutil.rmtree(temp_storage, ignore_errors=True)

@pytest.fixture
def mock_camera_controller() -> MagicMock:
    """Fixture for a mocked CameraController."""
    controller = MagicMock()
    controller.settings = MagicMock()
    controller.settings.model = MagicMock()
    controller.settings.model = CameraSettings(shutter=400)
    controller.settings.orientation_flag = 1
    preview_buffer = io.BytesIO()
    Image.new("RGB", (1, 1), color=(0, 0, 0)).save(preview_buffer, format="JPEG")
    controller.preview.return_value = preview_buffer.getvalue()
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


@pytest.fixture
def focus_stacking_environment(project_manager: ProjectManager, sample_scan_model: Scan) -> dict:
    """Creates an on-disk scan structure ready for focus stacking tests."""

    project = project_manager.add_project(sample_scan_model.project_name)
    project.scans[f"scan{sample_scan_model.index:02d}"] = sample_scan_model
    save_project(project)

    project_path = Path(project.path)
    scan_dir = project_path / f"scan{sample_scan_model.index:02d}"
    scan_dir.mkdir(parents=True, exist_ok=True)
    (scan_dir / "stacked").mkdir(exist_ok=True)

    return {
        "project_manager": project_manager,
        "project": project,
        "scan": sample_scan_model,
        "scan_dir": scan_dir,
        "stacked_dir": scan_dir / "stacked",
    }


@pytest.fixture
def focus_stacking_batches(focus_stacking_environment: dict) -> dict[int, list[str]]:
    """Generates dummy batch image files in the scan directory."""

    scan_dir: Path = focus_stacking_environment["scan_dir"]
    batches: dict[int, list[str]] = {
        1: [],
        2: [],
    }

    for position in batches.keys():
        for stack_index in (1, 2):
            filename = f"scan{focus_stacking_environment['scan'].index:02d}_{position:03d}_fs{stack_index:02d}.jpg"
            file_path = scan_dir / filename
            file_path.write_bytes(b"dummy")
            batches[position].append(str(file_path))

    return batches


@pytest_asyncio.fixture
async def focus_task_manager():
    """Provides an isolated TaskManager instance for focus stacking tests."""

    if os.path.exists(TASKS_STORAGE_PATH):
        shutil.rmtree(TASKS_STORAGE_PATH)
    os.makedirs(TASKS_STORAGE_PATH, exist_ok=True)

    TaskManager._instance = None
    task_manager = TaskManager()
    task_manager.autodiscover_tasks(
        namespaces=["openscan_firmware.controllers.services.tasks"],
        include_subpackages=True,
        ignore_modules={"base_task", "task_manager", "example_tasks"},
        safe_mode=True,
        override_on_conflict=False,
        require_explicit_name=True,
        raise_on_missing_name=True,
    )

    yield task_manager

    active_tasks = task_manager.get_all_tasks_info()
    if active_tasks:
        cancellations = [
            task_manager.cancel_task(task.id)
            for task in active_tasks
            if task.status in {TaskStatus.RUNNING, TaskStatus.PENDING, TaskStatus.PAUSED}
        ]
        if cancellations:
            await asyncio.gather(*cancellations, return_exceptions=True)
            await asyncio.sleep(0.01)

    if os.path.exists(TASKS_STORAGE_PATH):
        shutil.rmtree(TASKS_STORAGE_PATH)

    TaskManager._instance = None


@pytest.fixture(autouse=True)
def cleanup_task_manager_storage(
    monkeypatch: pytest.MonkeyPatch,
    task_manager_storage_path,
):
    """Reset TaskManager state and persistence between individual tests."""

    task_manager_storage_path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "openscan_firmware.controllers.services.tasks.task_manager.TASKS_STORAGE_PATH",
        task_manager_storage_path,
        raising=False,
    )

    TaskManager._instance = None

    yield

    task_manager_instance = TaskManager._instance
    if task_manager_instance is not None:
        pending_handles = list(getattr(task_manager_instance, "_running_async_tasks", {}).values())
        pending_handles += list(getattr(task_manager_instance, "_running_blocking_tasks", {}).values())
        for handle in pending_handles:
            loop = handle.get_loop()
            if loop.is_closed():
                continue
            handle.cancel()

    TaskManager._instance = None
