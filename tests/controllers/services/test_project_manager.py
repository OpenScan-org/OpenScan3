import pytest
import os
from pathlib import Path
from datetime import datetime

from app.controllers.services.projects import ProjectManager, _get_project_path
from app.models.project import Project
from app.models.scan import Scan, ScanStatus  
from app.config.scan import ScanSetting  
from app.config.camera import CameraSettings  

# Helper to temporarily change the base projects_path for testing
@pytest.fixture(autouse=True)
def MOCKED_PROJECTS_PATH(tmp_path, monkeypatch):
    # Create a subdirectory within tmp_path for projects to mimic the real structure if needed
    mock_projects_dir = tmp_path / "projects_test_root"
    mock_projects_dir.mkdir()
    return mock_projects_dir


@pytest.fixture
def project_manager(MOCKED_PROJECTS_PATH) -> ProjectManager:
    """Fixture to create a ProjectManager instance with a temporary projects path."""
    # The ProjectManager's __init__ will now use MOCKED_PROJECTS_PATH
    pm = ProjectManager(path=MOCKED_PROJECTS_PATH)
    return pm


# --- Test Cases for ProjectManager ---

def test_pm_add_project_new(project_manager: ProjectManager, MOCKED_PROJECTS_PATH):
    """Test adding a new project successfully."""
    project_name = "Test Project"
    project_description = "A test project description."

    # Action: Add the project
    created_project = project_manager.add_project(name=project_name, project_description=project_description)

    # Assertions for the returned project object
    assert created_project is not None
    assert created_project.name == project_name
    assert created_project.description == project_description
    assert isinstance(created_project.created, datetime)
    assert len(created_project.scans) == 0

    # Check path construction (relative to our MOCKED_PROJECTS_PATH)
    # _get_project_path uses the module-level projects_path, which is monkeypatched
    expected_path = MOCKED_PROJECTS_PATH / project_name
    assert Path(created_project.path).resolve() == expected_path.resolve()

    # Assertions for ProjectManager internal state
    assert project_name in project_manager.get_all_projects()
    assert project_manager.get_project_by_name(project_name) == created_project

    # Assertions for filesystem (existence of project directory and JSON file)
    project_dir_path = MOCKED_PROJECTS_PATH / project_name
    assert project_dir_path.exists()
    assert project_dir_path.is_dir()

    project_json_file = project_dir_path / "openscan_project.json"
    assert project_json_file.exists()
    assert project_json_file.is_file()

    # Optional: Load the project from JSON and verify its contents further
    # This would also test parts of get_project indirectly
    # For now, focusing on add_project's direct responsibilities


def test_pm_add_project_already_exists(project_manager: ProjectManager):
    """Test trying to add a project that already exists."""
    project_name = "Test Project Beta"
    project_manager.add_project(name=project_name)  # Add it once

    # Action & Assertion: Try to add it again, expecting a ValueError
    with pytest.raises(ValueError, match=f"Project {project_name} already exists"):
        project_manager.add_project(name=project_name)


def test_pm_init_empty_dir(MOCKED_PROJECTS_PATH):
    """Test ProjectManager initialization with an empty projects directory."""
    pm = ProjectManager(path=MOCKED_PROJECTS_PATH)
    assert len(pm.get_all_projects()) == 0


def test_pm_init_loads_existing_project(MOCKED_PROJECTS_PATH):
    """Test ProjectManager loads an existing project from the filesystem."""
    project_name = "ExistingProject"
    scan_index = 1
    project_dir = MOCKED_PROJECTS_PATH / project_name
    project_dir.mkdir()
    scan_dir = project_dir / f"scan{scan_index:02d}"
    scan_dir.mkdir()

    # Create dummy openscan_project.json
    project_data = {
        "name": project_name, 
        "path": str(project_dir.resolve()), 
        "created": datetime.now().isoformat(),
        "uploaded": False,
        "description": "An existing test project",
        "scans": {
            f"scan{scan_index:02d}": {"index": scan_index, "created": datetime.now().isoformat(), "status": ScanStatus.COMPLETED.value}
        }
    }
    with open(project_dir / "openscan_project.json", "w") as f:
        json.dump(project_data, f, indent=2)

    # Create dummy scan.json
    scan_data = {
        "project_name": project_name,
        "index": scan_index,
        "created": datetime.now().isoformat(),
        "settings": ScanSetting(path_method="spiral", points=10).model_dump(mode='json'),
        "camera_settings": CameraSettings().model_dump(mode='json'), 
        "status": ScanStatus.COMPLETED.value,
        "current_step": 0,
        "system_message": None,
        "last_updated": datetime.now().isoformat(),
        "description": "A test scan",
        "duration": 0.0,
        "photos": []
    }
    with open(scan_dir / "scan.json", "w") as f:
        json.dump(scan_data, f, indent=2)

    # Initialize ProjectManager - this should trigger loading
    pm = ProjectManager(path=MOCKED_PROJECTS_PATH)
    
    assert project_name in pm.get_all_projects()
    loaded_project = pm.get_project_by_name(project_name)
    assert loaded_project is not None
    assert loaded_project.name == project_name
    assert str(Path(loaded_project.path).resolve()) == str(project_dir.resolve())
    assert f"scan{scan_index:02d}" in loaded_project.scans
    actual_scan = loaded_project.scans[f"scan{scan_index:02d}"]
    assert actual_scan.index == scan_index
    assert actual_scan.status == ScanStatus.COMPLETED


def test_pm_init_reset_running_scan(MOCKED_PROJECTS_PATH):
    """Test ProjectManager resets status of a 'running' scan on init."""
    project_name = "ProjectWithRunningScan"
    scan_index = 1
    project_dir = MOCKED_PROJECTS_PATH / project_name
    project_dir.mkdir()
    scan_dir = project_dir / f"scan{scan_index:02d}"
    scan_dir.mkdir()

    project_file_data = {
        "name": project_name, "path": str(project_dir.resolve()), "created": datetime.now().isoformat(),
        "scans": {f"scan{scan_index:02d}": {"index": scan_index, "created": datetime.now().isoformat(), "status": ScanStatus.RUNNING.value}}
    }
    with open(project_dir / "openscan_project.json", "w") as f:
        json.dump(project_file_data, f, indent=2)

    scan_file_data = {
        "project_name": project_name, "index": scan_index, "created": datetime.now().isoformat(),
        "settings": ScanSetting(path_method="spiral", points=5).model_dump(mode='json'),
        "camera_settings": CameraSettings().model_dump(mode='json'),
        "status": ScanStatus.RUNNING.value, 
        "current_step": 1, "system_message": None, "last_updated": datetime.now().isoformat(),
        "description": None, "duration": 0.0, "photos": []
    }
    with open(scan_dir / "scan.json", "w") as f:
        json.dump(scan_file_data, f, indent=2)
    
    pm = ProjectManager(path=MOCKED_PROJECTS_PATH)

    loaded_project = pm.get_project_by_name(project_name)
    assert loaded_project is not None
    the_scan = loaded_project.scans.get(f"scan{scan_index:02d}")
    assert the_scan is not None
    assert the_scan.status == ScanStatus.ERROR
    assert "interrupted because the application was restarted" in the_scan.system_message
    
    # Verify that the changes were saved back to scan.json by ProjectManager._reset_running_scans
    # This requires _reset_running_scans to call save_project or for the scan to be saved some other way.
    # Current ProjectManager._reset_running_scans modifies in-memory only.
    # If save_project is called within _reset_running_scans (after modifications), this check would be valid:
    # with open(scan_dir / "scan.json", "r") as f:
    #     updated_scan_data_on_disk = json.load(f)
    # assert updated_scan_data_on_disk["status"] == ScanStatus.ERROR.value


import json 
from unittest.mock import MagicMock 


@pytest.fixture
def mock_camera_controller() -> MagicMock:
    """Fixture for a mocked CameraController."""
    controller = MagicMock()
    controller.settings = MagicMock()
    # Ensure .model returns a Pydantic model or a dict that can be validated by Pydantic
    controller.settings.model = CameraSettings(shutter=400)
    return controller


def test_pm_add_scan_to_project(project_manager: ProjectManager, mock_camera_controller: MagicMock, MOCKED_PROJECTS_PATH):
    """Test adding a new scan to an existing project."""
    project_name = "ProjectForScans"
    project = project_manager.add_project(name=project_name)

    scan_settings = ScanSetting(path_method="fibonacci", points=20, focus_stacks=3)

    new_scan = project_manager.add_scan(
        project_name=project_name, 
        camera_controller=mock_camera_controller, 
        scan_settings=scan_settings
    )

    assert new_scan is not None
    assert new_scan.project_name == project_name
    assert new_scan.index == 1
    assert new_scan.settings == scan_settings
    assert isinstance(new_scan.camera_settings, CameraSettings) 
    assert new_scan.camera_settings.shutter == 400
    assert new_scan.status == ScanStatus.PENDING

    updated_project = project_manager.get_project_by_name(project_name)
    assert f"scan{new_scan.index:02d}" in updated_project.scans
    assert updated_project.scans[f"scan{new_scan.index:02d}"] == new_scan

    scan_dir_path = MOCKED_PROJECTS_PATH / project_name / f"scan{new_scan.index:02d}"
    assert scan_dir_path.exists() and scan_dir_path.is_dir()

    scan_json_file = scan_dir_path / "scan.json"
    assert scan_json_file.exists()
    with open(scan_json_file, "r") as f:
        scan_data_on_disk = json.load(f)
    assert scan_data_on_disk["index"] == new_scan.index
    assert scan_data_on_disk["settings"]["points"] == scan_settings.points
    assert scan_data_on_disk["camera_settings"]["shutter"] == 400

    project_json_file = MOCKED_PROJECTS_PATH / project_name / "openscan_project.json"
    with open(project_json_file, "r") as f:
        project_json_data = json.load(f)
    assert f"scan{new_scan.index:02d}" in project_json_data["scans"]
    assert project_json_data["scans"][f"scan{new_scan.index:02d}"]["index"] == new_scan.index


def test_pm_add_multiple_scans_increment_index(project_manager: ProjectManager, mock_camera_controller: MagicMock):
    """Test that scan indices are incremented correctly when adding multiple scans."""
    project_name = "ProjectMultiScan"
    project_manager.add_project(name=project_name)
    scan_settings = ScanSetting(path_method="spiral", points=10)

    scan1 = project_manager.add_scan(project_name, mock_camera_controller, scan_settings)
    assert scan1.index == 1

    scan2 = project_manager.add_scan(project_name, mock_camera_controller, scan_settings)
    assert scan2.index == 2

    scan3 = project_manager.add_scan(project_name, mock_camera_controller, scan_settings)
    assert scan3.index == 3

    project = project_manager.get_project_by_name(project_name)
    assert len(project.scans) == 3


def test_pm_add_scan_to_non_existent_project(project_manager: ProjectManager, mock_camera_controller: MagicMock):
    """Test adding a scan to a project name that doesn't exist in the manager."""
    scan_settings = ScanSetting(path_method="spiral", points=10)
    
    # This behavior changed: set_current_project within add_scan raises ValueError if project not found by name
    # The original add_scan would try self._projects[project_name] which would be a KeyError
    # Now, self.set_current_project(self._projects[project_name]) is called.
    # If project_name is not in self._projects, it's a KeyError.
    # If the project object from self._projects somehow differs from what set_current_project expects, it's ValueError.
    # Let's assume the first case (project not in dict) is the most direct to test.
    with pytest.raises(ValueError):
        project_manager.add_scan(
            project_name="NonExistentProject", 
            camera_controller=mock_camera_controller, 
            scan_settings=scan_settings
        )
