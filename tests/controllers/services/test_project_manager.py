import pytest
import os
import json
from pathlib import Path
from datetime import datetime
from unittest.mock import MagicMock

from app.controllers.services.projects import ProjectManager
from app.models.project import Project
from app.models.scan import Scan
from app.config.scan import ScanSetting
from app.config.camera import CameraSettings


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


# --- Test Cases for ProjectManager ---

@pytest.mark.asyncio
async def test_pm_add_project_new(project_manager: ProjectManager, MOCKED_PROJECTS_PATH):
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

    expected_path = MOCKED_PROJECTS_PATH / project_name
    assert Path(created_project.path).resolve() == expected_path.resolve()

    # Assertions for ProjectManager internal state (synchronous calls)
    all_projects = project_manager.get_all_projects()
    assert project_name in all_projects
    assert project_manager.get_project_by_name(project_name) == created_project

    # Assertions for filesystem
    project_dir_path = MOCKED_PROJECTS_PATH / project_name
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
async def test_pm_init_empty_dir(MOCKED_PROJECTS_PATH):
    """Test ProjectManager initialization with an empty projects directory."""
    pm = ProjectManager(path=MOCKED_PROJECTS_PATH)
    # The loading happens in __init__, so we check the result right after (synchronous call).
    all_projects = pm.get_all_projects()
    assert len(all_projects) == 0


@pytest.mark.asyncio
async def test_pm_init_loads_existing_project(MOCKED_PROJECTS_PATH):
    """Test ProjectManager loads an existing project from the filesystem on initialization."""
    project_name = "ExistingProject"
    scan_index = 1
    project_dir = MOCKED_PROJECTS_PATH / project_name
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
        "settings": ScanSetting(path_method="fibonacci", points=10).model_dump(mode='json'),
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
    pm = ProjectManager(path=MOCKED_PROJECTS_PATH)
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


@pytest.fixture
def mock_camera_controller() -> MagicMock:
    """Fixture for a mocked CameraController."""
    controller = MagicMock()
    controller.name = "mock_camera"
    # Simulate the structure that add_scan_to_project expects
    controller.settings = CameraSettings(shutter=400)
    return controller


@pytest.mark.asyncio
async def test_pm_add_scan_to_project(project_manager: ProjectManager, mock_camera_controller: MagicMock, MOCKED_PROJECTS_PATH):
    """Test adding a new scan to an existing project."""
    project_name = "ProjectForScanning"
    project_manager.add_project(name=project_name) # Synchronous call

    scan_settings = ScanSetting(points=50, path_method="fibonacci", )
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
    assert new_scan.settings.points == 50
    assert new_scan.camera_settings.shutter == 400 # From mock_camera_controller
    assert new_scan.current_step == 0
    assert new_scan.duration == 0.0

    # Assertions for filesystem
    scan_dir = MOCKED_PROJECTS_PATH / project_name / "scan01"
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
        scan_settings=ScanSetting(points=100, path_method="fibonacci"),
        camera_controller=mock_camera_controller
    )
    assert second_scan.index == 2
    project = project_manager.get_project_by_name(project_name) # Synchronous call
    assert "scan02" in project.scans
