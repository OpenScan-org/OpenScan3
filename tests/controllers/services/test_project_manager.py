import asyncio
import io
import json
import os
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from openscan_firmware.controllers.services.projects import (
    ProjectManager,
    _write_json_atomic,
    save_project,
)
from openscan_firmware.config.camera import CameraSettings
from openscan_firmware.config.scan import ScanSetting
from openscan_firmware.models.camera import PhotoData
from openscan_firmware.models.paths import PolarPoint3D
from openscan_firmware.models.scan import Scan, ScanMetadata, StackingTaskStatus
from openscan_firmware.models.task import TaskStatus


# --- Helper utilities ------------------------------------------------------


def _scan_directory(project_manager: ProjectManager, project_name: str, scan_index: int) -> Path:
    project = project_manager.get_project_by_name(project_name)
    assert project is not None, "Project must exist for scan directory lookup"
    return Path(project.path) / f"scan{scan_index:02d}"


def _dir_size(directory: Path) -> int:
    return sum(file.stat().st_size for file in directory.rglob("*") if file.is_file())


# --- Test Cases for ProjectManager ---

@pytest.mark.asyncio
async def test_pm_add_project_new(project_manager: ProjectManager):
    """Test adding a new project successfully."""
    project_name = "Test Project"
    project_description = "A test project description."

    # Action: Add the project (synchronous call)
    created_project = project_manager.add_project(name=project_name, project_description=project_description)

    # Assertions for the returned project object
    assert created_project is not None
    assert created_project.name == project_name
    assert created_project.description == project_description
    assert isinstance(created_project.created, datetime)
    assert len(created_project.scans) == 0

    expected_path = Path(project_manager._path) / project_name
    assert Path(created_project.path).resolve() == expected_path.resolve()

    # Assertions for ProjectManager internal state (synchronous calls)
    all_projects = project_manager.get_all_projects()
    assert project_name in all_projects
    assert project_manager.get_project_by_name(project_name) == created_project

    # Assertions for filesystem
    project_dir_path = Path(project_manager._path) / project_name
    assert project_dir_path.exists() and project_dir_path.is_dir()
    project_json_file = project_dir_path / "openscan_project.json"
    assert project_json_file.exists() and project_json_file.is_file()


@pytest.mark.asyncio
async def test_pm_add_project_already_exists(project_manager: ProjectManager):
    """Test trying to add a project that already exists."""
    project_name = "Test Project Beta"
    project_manager.add_project(name=project_name)  # Add it once (synchronous call)

    # Action & Assertion: Try to add it again, expecting a ValueError
    with pytest.raises(ValueError, match=f"Project {project_name} already exists"):
        project_manager.add_project(name=project_name) # Synchronous call


@pytest.mark.asyncio
async def test_pm_init_empty_dir(project_manager: ProjectManager):
    """Test ProjectManager initialization with an empty projects directory."""
    # The loading happens in __init__, so we check the result right after (synchronous call).
    all_projects = project_manager.get_all_projects()
    assert len(all_projects) == 0


@pytest.mark.asyncio
async def test_pm_init_loads_existing_project(project_manager: ProjectManager,
                                              sample_scan_settings: ScanSetting):
    """Test ProjectManager loads an existing project from the filesystem on initialization."""
    project_name = "ExistingProject"
    scan_index = 1
    project_dir = Path(project_manager._path) / project_name
    project_dir.mkdir()
    scan_dir = project_dir / f"scan{scan_index:02d}"
    scan_dir.mkdir()

    # Create dummy openscan_project.json (without status)
    project_data = {
        "name": project_name,
        "path": str(project_dir.resolve()),
        "created": datetime.now().isoformat(),
        "uploaded": False,
        "description": "An existing test project",
        "scans": {
            f"scan{scan_index:02d}": {"index": scan_index, "created": datetime.now().isoformat()}
        }
    }
    with open(project_dir / "openscan_project.json", "w") as f:
        json.dump(project_data, f, indent=2)

    # Create dummy scan.json (without status)
    scan_data = {
        "project_name": project_name,
        "index": scan_index,
        "created": datetime.now().isoformat(),
        "settings": sample_scan_settings.model_dump(mode='json'),
        "camera_settings": CameraSettings().model_dump(mode='json'),
        "current_step": 10,
        "system_message": None,
        "last_updated": datetime.now().isoformat(),
        "description": "A test scan",
        "duration": 120.5,
        "photos": []
    }
    with open(scan_dir / "scan.json", "w") as f:
        json.dump(scan_data, f, indent=2)

    # Initialize ProjectManager - this should trigger loading from disk
    pm = ProjectManager(path=Path(project_manager._path))
    all_projects = pm.get_all_projects() # Synchronous call
    assert project_name in all_projects

    loaded_project = pm.get_project_by_name(project_name) # Synchronous call
    assert loaded_project is not None
    assert loaded_project.name == project_name
    assert str(Path(loaded_project.path).resolve()) == str(project_dir.resolve())
    assert f"scan{scan_index:02d}" in loaded_project.scans

    actual_scan = loaded_project.scans[f"scan{scan_index:02d}"]
    assert actual_scan.index == scan_index
    assert actual_scan.duration == 120.5 # Verify other fields are loaded correctly
    assert actual_scan.current_step == 10


@pytest.mark.parametrize("initial_status", [TaskStatus.RUNNING, TaskStatus.PENDING])
def test_pm_recovers_incomplete_scans(
    tmp_path: Path, sample_scan_settings: ScanSetting, initial_status: TaskStatus
):
    project_name = "RecoverProject"
    scan_index = 1

    project_dir = tmp_path / project_name
    project_dir.mkdir()
    scan_dir = project_dir / f"scan{scan_index:02d}"
    scan_dir.mkdir()

    now_iso = datetime.now().isoformat()

    project_payload = {
        "name": project_name,
        "path": str(project_dir.resolve()),
        "created": now_iso,
        "uploaded": False,
        "scans": {
            f"scan{scan_index:02d}": {
                "index": scan_index,
                "created": now_iso,
            }
        },
    }
    with (project_dir / "openscan_project.json").open("w") as handle:
        json.dump(project_payload, handle, indent=2)

    scan_payload = {
        "project_name": project_name,
        "index": scan_index,
        "created": now_iso,
        "status": initial_status.value,
        "settings": sample_scan_settings.model_dump(mode="json"),
        "camera_settings": CameraSettings().model_dump(mode="json"),
        "current_step": 0,
        "last_updated": now_iso,
        "photos": [],
    }
    scan_file = scan_dir / "scan.json"
    with scan_file.open("w") as handle:
        json.dump(scan_payload, handle, indent=2)

    manager = ProjectManager(path=tmp_path)
    recovered_scan = manager.get_scan_by_index(project_name, scan_index)

    assert recovered_scan is not None
    assert recovered_scan.status == TaskStatus.INTERRUPTED

    with scan_file.open() as handle:
        persisted_payload = json.load(handle)

    assert persisted_payload["status"] == TaskStatus.INTERRUPTED.value


# @pytest.fixture
# def mock_camera_controller() -> MagicMock:
#     """Fixture for a mocked CameraController."""


@pytest.mark.asyncio
async def test_pm_add_scan_to_project(
    project_manager: ProjectManager,
    mock_camera_controller: MagicMock,
    sample_scan_settings: ScanSetting,
):
    """Test adding a new scan to an existing project."""
    project_name = "ProjectForScanning"
    project_manager.add_project(name=project_name) # Synchronous call

    scan_settings = sample_scan_settings
    scan_description = "First scan"

    # Action: Add the scan (asynchronous call)
    new_scan = project_manager.add_scan(
        project_name=project_name,
        scan_settings=scan_settings,
        camera_controller=mock_camera_controller,
        scan_description=scan_description
    )

    # Assertions for the returned Scan object
    assert new_scan is not None
    assert new_scan.index == 1
    assert new_scan.project_name == project_name
    assert new_scan.description == scan_description
    assert new_scan.settings.points == 10
    assert new_scan.camera_settings.shutter == 400 # From mock_camera_controller
    assert new_scan.current_step == 0
    assert new_scan.duration == 0.0

    # Assertions for filesystem
    scan_dir = Path(project_manager._path) / project_name / "scan01"
    assert scan_dir.exists() and scan_dir.is_dir()
    scan_json_file = scan_dir / "scan.json"
    assert scan_json_file.exists() and scan_json_file.is_file()

    # Verify data in ProjectManager's memory
    project = project_manager.get_project_by_name(project_name) # Synchronous call
    assert "scan01" in project.scans
    assert project.scans["scan01"].index == 1

    # Add a second scan to test indexing (asynchronous call)
    second_scan = project_manager.add_scan(
        project_name=project_name,
        scan_settings=sample_scan_settings,
        camera_controller=mock_camera_controller
    )
    assert second_scan.index == 2
    project = project_manager.get_project_by_name(project_name) # Synchronous call
    assert "scan02" in project.scans


@pytest.mark.asyncio
async def test_pm_save_scan_state_persists_stacking_status(
    project_manager: ProjectManager,
    mock_camera_controller: MagicMock,
    sample_scan_settings: ScanSetting,
):
    """Ensure stacking task status is written to disk and survives reload."""

    project_name = "StackingProject"
    project_manager.add_project(name=project_name)

    scan = project_manager.add_scan(
        project_name=project_name,
        scan_settings=sample_scan_settings,
        camera_controller=mock_camera_controller,
    )

    scan.stacking_task_status = StackingTaskStatus(
        task_id="stack-123",
        status=TaskStatus.RUNNING,
    )

    await project_manager.save_scan_state(scan)

    scan_json = Path(project_manager._path) / project_name / "scan01" / "scan.json"
    with scan_json.open() as f:
        payload = json.load(f)

    assert payload["stacking_task_status"] == {
        "task_id": "stack-123",
        "status": TaskStatus.RUNNING.value,
    }

    reloaded_manager = ProjectManager(path=Path(project_manager._path))
    reloaded_scan = reloaded_manager.get_scan_by_index(project_name, scan.index)

    assert reloaded_scan is not None
    assert reloaded_scan.stacking_task_status == scan.stacking_task_status


def test_pm_ensure_scan_sizes_updates_total_size(
    project_manager: ProjectManager,
    sample_scan_settings: ScanSetting,
):
    project_name = "SizeSync"
    project = project_manager.add_project(name=project_name)

    scan = Scan(
        project_name=project_name,
        index=1,
        created=datetime.now(),
        settings=sample_scan_settings,
        camera_settings=CameraSettings(),
    )
    project.scans["scan01"] = scan
    save_project(project)

    scan_dir = _scan_directory(project_manager, project_name, scan.index)
    photo_path = scan_dir / "scan01_001.jpg"
    metadata_dir = scan_dir / "metadata"
    metadata_dir.mkdir(exist_ok=True)
    metadata_path = metadata_dir / "scan01_001.json"

    photo_path.write_bytes(b"x" * 128)
    metadata_path.write_text("{}")

    scan.total_size_bytes = 0

    project_manager._ensure_scan_sizes(project)

    expected_size = _dir_size(scan_dir)

    assert scan.total_size_bytes == expected_size

    scan_json = scan_dir / "scan.json"
    with scan_json.open() as handle:
        payload = json.load(handle)
    assert payload["total_size_bytes"] == expected_size


@pytest.mark.asyncio
async def test_pm_add_photo_async_updates_total_size(
    project_manager: ProjectManager,
    mock_camera_controller: MagicMock,
    sample_scan_settings: ScanSetting,
    sample_camera_metadata,
):
    project_name = "PhotoGrowth"
    project_manager.add_project(name=project_name)
    scan = project_manager.add_scan(
        project_name=project_name,
        camera_controller=mock_camera_controller,
        scan_settings=sample_scan_settings,
    )

    project = project_manager.get_project_by_name(project_name)
    assert project is not None
    scan_dir = _scan_directory(project_manager, project_name, scan.index)
    initial_size = project_manager._calculate_scan_size_bytes(project, scan)

    photo_data = PhotoData(
        data=io.BytesIO(b"photo-bytes"),
        format="jpeg",
        camera_metadata=sample_camera_metadata,
    )
    photo_data.scan_metadata = ScanMetadata(
        step=1,
        polar_coordinates=PolarPoint3D(theta=0.0, fi=0.0, r=1.0),
        project_name=project_name,
        scan_index=scan.index,
    )

    await project_manager.add_photo_async(photo_data)

    updated_scan = project_manager.get_scan_by_index(project_name, scan.index)
    project = project_manager.get_project_by_name(project_name)
    assert project is not None
    updated_size = project_manager._calculate_scan_size_bytes(project, updated_scan)

    assert updated_scan is not None
    assert updated_scan.total_size_bytes == updated_size
    assert updated_size > initial_size


@pytest.mark.asyncio
async def test_pm_add_photo_async_tracks_filename(
    project_manager: ProjectManager,
    mock_camera_controller: MagicMock,
    sample_scan_settings: ScanSetting,
    sample_camera_metadata,
):
    project_name = "PhotoIndex"
    project_manager.add_project(name=project_name)
    scan = project_manager.add_scan(
        project_name=project_name,
        camera_controller=mock_camera_controller,
        scan_settings=sample_scan_settings,
    )

    photo_data = PhotoData(
        data=io.BytesIO(b"photo-bytes"),
        format="jpeg",
        camera_metadata=sample_camera_metadata,
    )
    photo_data.scan_metadata = ScanMetadata(
        step=1,
        polar_coordinates=PolarPoint3D(theta=0.0, fi=0.0, r=1.0),
        project_name=project_name,
        scan_index=scan.index,
    )

    await project_manager.add_photo_async(photo_data)

    updated_scan = project_manager.get_scan_by_index(project_name, scan.index)
    assert updated_scan is not None
    expected_filename = f"scan{scan.index:02d}_{1:03d}.jpg"
    assert updated_scan.photos == [expected_filename]

    scan_dir = _scan_directory(project_manager, project_name, scan.index)
    assert (scan_dir / expected_filename).exists()
    assert (scan_dir / "metadata" / "scan01_001.json").exists()


@pytest.mark.asyncio
async def test_pm_delete_photos_recalculates_total_size(
    project_manager: ProjectManager,
    mock_camera_controller: MagicMock,
    sample_scan_settings: ScanSetting,
    sample_camera_metadata,
):
    project_name = "PhotoShrink"
    project_manager.add_project(name=project_name)
    scan = project_manager.add_scan(
        project_name=project_name,
        camera_controller=mock_camera_controller,
        scan_settings=sample_scan_settings,
    )

    photo_data = PhotoData(
        data=io.BytesIO(b"photo-bytes"),
        format="jpeg",
        camera_metadata=sample_camera_metadata,
    )
    photo_data.scan_metadata = ScanMetadata(
        step=1,
        polar_coordinates=PolarPoint3D(theta=0.0, fi=0.0, r=1.0),
        project_name=project_name,
        scan_index=scan.index,
    )

    await project_manager.add_photo_async(photo_data)

    project = project_manager.get_project_by_name(project_name)
    assert project is not None
    scan_dir = _scan_directory(project_manager, project_name, scan.index)
    photo_filename = f"scan{scan.index:02d}_{1:03d}.jpg"
    added_size = project_manager._calculate_scan_size_bytes(project, scan)
    assert (scan_dir / photo_filename).exists()

    scan = project_manager.get_scan_by_index(project_name, scan.index)
    assert scan is not None

    project_manager.delete_photos(scan, [photo_filename])

    reduced_scan = project_manager.get_scan_by_index(project_name, scan.index)
    project = project_manager.get_project_by_name(project_name)
    assert project is not None
    reduced_size = project_manager._calculate_scan_size_bytes(project, reduced_scan)

    assert not (scan_dir / photo_filename).exists()
    assert reduced_size < added_size
    assert reduced_scan is not None
    assert reduced_scan.total_size_bytes == reduced_size


@pytest.mark.asyncio
async def test_pm_delete_photos_updates_photo_index(
    project_manager: ProjectManager,
    mock_camera_controller: MagicMock,
    sample_scan_settings: ScanSetting,
    sample_camera_metadata,
):
    project_name = "PhotoDelete"
    project_manager.add_project(name=project_name)
    scan = project_manager.add_scan(
        project_name=project_name,
        camera_controller=mock_camera_controller,
        scan_settings=sample_scan_settings,
    )

    photo_data = PhotoData(
        data=io.BytesIO(b"photo-bytes"),
        format="jpeg",
        camera_metadata=sample_camera_metadata,
    )
    photo_data.scan_metadata = ScanMetadata(
        step=1,
        polar_coordinates=PolarPoint3D(theta=0.0, fi=0.0, r=1.0),
        project_name=project_name,
        scan_index=scan.index,
    )

    await project_manager.add_photo_async(photo_data)
    scan = project_manager.get_scan_by_index(project_name, scan.index)
    assert scan is not None
    photo_filename = f"scan{scan.index:02d}_{1:03d}.jpg"

    project_manager.delete_photos(scan, [photo_filename])

    updated_scan = project_manager.get_scan_by_index(project_name, scan.index)
    assert updated_scan is not None
    assert photo_filename not in updated_scan.photos

    scan_dir = _scan_directory(project_manager, project_name, scan.index)
    assert not (scan_dir / photo_filename).exists()
    assert not (scan_dir / "metadata" / "scan01_001.json").exists()


@pytest.mark.asyncio
async def test_pm_get_photo_file_returns_metadata(
    project_manager: ProjectManager,
    mock_camera_controller: MagicMock,
    sample_scan_settings: ScanSetting,
    sample_camera_metadata,
):
    project_name = "PhotoFetch"
    project_manager.add_project(name=project_name)
    scan = project_manager.add_scan(
        project_name=project_name,
        camera_controller=mock_camera_controller,
        scan_settings=sample_scan_settings,
    )

    photo_data = PhotoData(
        data=io.BytesIO(b"photo-bytes"),
        format="jpeg",
        camera_metadata=sample_camera_metadata,
    )
    photo_data.scan_metadata = ScanMetadata(
        step=2,
        polar_coordinates=PolarPoint3D(theta=0.0, fi=0.0, r=1.0),
        project_name=project_name,
        scan_index=scan.index,
    )

    await project_manager.add_photo_async(photo_data)
    photo_filename = f"scan{scan.index:02d}_{2:03d}.jpg"

    stored_scan, photo_path, metadata = project_manager.get_photo_file(
        project_name,
        scan.index,
        photo_filename,
    )

    assert stored_scan.index == scan.index
    assert photo_filename in stored_scan.photos
    assert os.path.basename(photo_path) == photo_filename
    assert metadata is not None
    assert metadata["scan_metadata"]["step"] == 2
