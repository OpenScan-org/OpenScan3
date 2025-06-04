from datetime import datetime
import io
import asyncio
from fileinput import filename
from typing import Optional

import aiofiles
import pathlib
from tempfile import TemporaryFile
from typing import IO
from zipfile import ZipFile
import json
import os
import shutil
from io import BytesIO

from app.models.project import Project
from app.models.scan import Scan, ScanStatus
from app.controllers.hardware.cameras.camera import CameraController
from app.config.scan import ScanSetting
from app.models.paths import PathMethod

ALLOWED_EXTENSIONS = (".jpg", ".jpeg", ".png")


def get_projects(projects_path: str) -> list[Project]:
    """Get all projects in the projects directory"""
    projects = []
    for folder in os.listdir(projects_path):
        project_json = os.path.join(projects_path, folder, "openscan_project.json")
        if os.path.exists(project_json):
            try:
                projects.append(get_project(projects_path, folder))
            except Exception as e:
                print(f"Error loading project {folder}: {e}")
    return projects


def _get_project_path(projects_path: str, project_name: str) -> str:
    """Get the absolute path for a project"""
    return os.path.join(str(projects_path), project_name)


def _get_project_photos(project_path: str) -> list[str]:
    """Get list of photos in a project directory"""
    return [
        file for file in os.listdir(project_path)
        if file.lower().endswith(ALLOWED_EXTENSIONS)
    ]


def get_project(projects_path: str, project_name: str) -> Project:
    """Load a project from disk including all scan data"""
    project_path = _get_project_path(projects_path, project_name)
    project_json = os.path.join(project_path, "openscan_project.json")

    if not os.path.exists(project_json):
        raise FileNotFoundError(f"Project {project_name} not found")

    # Load project data
    with open(project_json, "rb") as f:
        project_data = json.loads(f.read())

    # Load scan data from their respective folders
    scans = {}
    if "scans" in project_data:
        for scan_id, scan_summary in project_data["scans"].items():
            try:
                scan = _load_scan_json(projects_path, project_name, scan_summary["index"])
                scans[scan_id] = scan
            except FileNotFoundError as e:
                print(f"Warning: Could not load scan {scan_id}: {e}")

    # Create project with string path
    return Project(
        name=project_name,
        path=project_path,
        created=project_data.get("created"),
        uploaded=project_data.get("uploaded", False),
        scans=scans,
        description=project_data.get("description")
    )


def delete_project(project: Project) -> bool:
    """Delete a project from file system"""
    try:
        if os.path.exists(project.path):
            shutil.rmtree(project.path)
            return True
        return False
    except Exception as e:
        print(f"Error deleting project: {e}")
        return False

def _scan_exists(project: Project, scan_index: int) -> bool:
    """Check if a scan exists in a project"""
    scan_id = f"scan{scan_index:02d}"
    return scan_id in project.scans


def _save_scan_json(projects_path: str, scan: Scan) -> None:
    """Save a scan to a separate JSON file in the scan directory"""
    # Create a new folder if necessary
    #base_project_path = _get_project_path(projects_path, scan.project_name) # Get base path for the project
    scan_folder_path = os.path.join(projects_path, f"scan{scan.index:02d}")
    os.makedirs(scan_folder_path, exist_ok=True)

    scan_json_data = scan.model_dump_json(indent=2)

    scan_file_path = os.path.join(scan_folder_path, "scan.json")
    with open(scan_file_path, "w") as f:
        f.write(scan_json_data)


def _load_scan_json(project_path: str, project_name: str, scan_index: int) -> Scan:
    """Load a scan from its JSON file"""
    # Construct the full path for the scan directory and file
    base_project_path = _get_project_path(project_path,project_name)
    scan_folder_path = os.path.join(base_project_path, f"scan{scan_index:02d}")
    scan_file_path = os.path.join(scan_folder_path, "scan.json")

    if not os.path.exists(scan_file_path):
        raise FileNotFoundError(f"Scan file not found: {scan_file_path}")

    with open(scan_file_path, "r") as f:  # Open in text mode "r"
        scan_json_data = f.read()

    # Recreate the Scan object using Pydantic's model_validate_json
    return Scan.model_validate_json(scan_json_data)


def save_project(project: Project):
    """Save the project metadata to openscan_project.json and each scan to its individual file."""
    project_json_path = os.path.join(project.path, "openscan_project.json")

    # Create project directory if it doesn't exist (e.g., for a new project)
    os.makedirs(project.path, exist_ok=True)

    # Prepare the main project data for openscan_project.json
    # We use model_dump to get a serializable dictionary, especially for datetime fields.
    # Exclude 'scans' as we'll handle its summary representation manually.
    project_main_data = project.model_dump(mode='json', exclude={'scans'})

    # Create a summary for the scans to be stored in openscan_project.json
    # This summary typically includes just the index or other minimal identifying info.
    scans_summary = {}
    for scan_id, scan_obj in project.scans.items():
        scans_summary[scan_id] = {"index": scan_obj.index,
                                  "created": scan_obj.created.isoformat(),
                                  "status": scan_obj.status.value
                                  }  # Add more summary fields if needed

    project_main_data['scans'] = scans_summary

    # Write the main project data to openscan_project.json
    with open(project_json_path, "w") as f:
        json.dump(project_main_data, f, indent=2)

    # Save each scan to its individual JSON file using the refactored _save_scan_json
    for scan in project.scans.values():
        _save_scan_json(project.path, scan)  # scan.project_name should be correctly set in the Scan object

    # Write the main project data to openscan_project.json
    with open(project_json_path, "w") as f:
        json.dump(project_main_data, f, indent=2)

    # Save each scan to its individual JSON file using the refactored _save_scan_json
    for scan in project.scans.values():
        _save_scan_json(project.path, scan)  # scan.project_name should be correctly set in the Scan object



class ProjectManager:
    def __init__(self, path=pathlib.PurePath("projects")):
        """Initialize project manager with base path"""
        self._path = str(path)  # Ensure string path
        self._projects = {}

        # Load existing projects
        for folder in os.listdir(self._path):
            project_json = os.path.join(self._path, folder, "openscan_project.json")
            if os.path.isdir(os.path.join(self._path, folder)) and os.path.exists(project_json):
                try:
                    self._projects[folder] = get_project(self._path, folder)
                except Exception as e:
                    print(f"Error loading project {folder}: {e}")

        # Reset any running scans to a failed state (app was restarted)
        self._reset_running_scans()

    def _reset_running_scans(self):
        """Reset any running scans to failed state when app starts"""
        running_scans_found = False

        for project_name, project in self._projects.items():
            for scan_id, scan in project.scans.items():
                if scan.status == ScanStatus.RUNNING or scan.status == ScanStatus.PAUSED:
                    # App was restarted while scan was running
                    error_msg = "Scan was interrupted because the application was restarted"
                    scan.status = ScanStatus.ERROR
                    scan.system_message = error_msg
                    scan.last_updated = datetime.now()
                    running_scans_found = True

        if running_scans_found:
            print("Warning: Found interrupted scans that were reset to ERROR state")

    def get_project_by_name(self, project_name: str) -> Optional[Project]:
        """Get a project by name. Returns None if the project does not exist."""
        if project_name not in self._projects:
            print(f"Project {project_name} does not exist")
            return None
        return self._projects[project_name]

    def get_all_projects(self) -> dict[str, Project]:
        """Get all projects as a dictionary of project name to a project object"""
        return self._projects


    def add_project(self, name: str, project_description=None) -> Project:
        """Create a new project.

        Args:
            name: The name of the project.
            project_description: Optional description for the project.

        Returns:
            The newly created project.
        """
        if name in self._projects.keys():
            raise ValueError(f"Project {name} already exists")

        project_path = _get_project_path(self._path,name)

        # Create project object (Note: This will validate name, path.)
        project = Project(
            name=name,
            path=project_path,
            created=datetime.now(),
            scans={},
            description=project_description
        )

        # Save project to disk
        save_project(project)

        # Update manager state
        self._projects[name] = project

        return project


    def delete_project(self, project: Project) -> bool:
        """Delete a project from the filesystem.

        Args:
            project: The project to delete.

        Returns:
            True if the project was successfully deleted, False otherwise.

        """
        if delete_project(project):
            del self._projects[project.name]
            return True
        return False

    def add_scan(self, project_name: str,
                 camera_controller: CameraController,
                 scan_settings: ScanSetting,
                 scan_description = None,) -> Optional[Scan]:
        """Add a new scan to a project.

        Args:
            project_name: The name of the project to add the scan to.
            camera_controller: The camera controller to use for the scan.
            scan_settings: The settings for the scan.
            scan_description: Optional description for the scan.

        Returns:
            The newly created scan if successful, None if not.
        """
        project = self.get_project_by_name(project_name)
        if not project:
            raise ValueError(f"Project {project_name} does not exist")

        if project.scans:
            sorted_scans = sorted(project.scans.values(), key=lambda scan: scan.index)
            new_index = sorted_scans[-1].index + 1
        else:
            new_index = 1

        os.makedirs(os.path.join(project.path, f"scan{new_index:02d}"), exist_ok=True)

        scan = Scan(
            project_name=project.name,
            index=new_index,
            created=datetime.now(),
            settings=scan_settings,
            description=scan_description,
            camera_settings=camera_controller.settings.model
        )


        project.scans[f"scan{new_index:02d}"] = scan

        save_project(project)

        return scan

    async def add_photo_async(self, scan: Scan, photo, photo_info: dict) -> None:
        """Asynchronously save a photo to the current project """
        photo = BytesIO(photo)
        photo.seek(0)
        photo_data = photo.read()

        project = self.get_project_by_name(scan.project_name)

        photo_path = os.path.join(project.path, f"scan{scan.index:02d}")
        photo_filename = f"scan{scan.index:02d}_{photo_info['position']:03d}"

        # save focus stacking photos
        if photo_info.get("stack_index") is not None:
            photo_filename = photo_filename + f"_fs{photo_info['stack_index']:02d}.jpg"
        # save without focus stacking
        else:
            photo_filename = photo_filename + ".jpg"


        async with aiofiles.open(os.path.join(photo_path, photo_filename), "wb") as f:
            await f.write(photo_data)
            scan.photos.append(photo_filename)

        save_project(project)

    def save(self, scan: Scan):
        project = get_project(self._path,scan.project_name)
        save_project(project)


    def get_scan_by_index(self, project_name: str, scan_index: int) -> Optional[Scan]:
        """Get a scan by its index from a project

        :return: The scan object with the given index if it exists, None if not
        """
        try:
            project = self._projects[project_name]

            # Check if scan exists
            scan_id = f"scan{scan_index:02d}"
            if scan_id not in project.scans:
                print(f"Scan {scan_id} does not exist in project {project_name}")
                return None

            return project.scans[scan_id]
        except Exception as e:
            print(f"Error getting scan: {e}")
            return None

    def delete_scan(self, scan: Scan) -> bool:
        """Delete a scan from a project

        Args:
            scan (Scan): The scan to delete

        Returns:
            bool: True if the scan was successfully deleted, False otherwise
        """
        try:
            project = self._projects[scan.project_name]
            scan_id = f"scan{scan.index:02d}"

            # Delete the scan directory and remove scan from the project
            scan_path = os.path.join(project.path, scan_id)
            if os.path.exists(scan_path):
                shutil.rmtree(scan_path)
            del project.scans[scan_id]

            save_project(project)

            return True
        except Exception as e:
            print(f"Error deleting scan: {e}")
            return False

    def delete_photos(self, scan: Scan, photo_filenames: list[str]) -> bool:
        """Delete one or more photos from a scan in a project"""
        try:
            scan_id = f"scan{scan.index:02d}"
            photo_path = os.path.join(scan.project_name, scan_id)

            for photo_filename in photo_filenames:
                photo_path = os.path.join(photo_path, photo_filename)
                if os.path.exists(photo_path):
                    os.remove(photo_path)

            return True
        except Exception as e:
            print(f"Error deleting photo: {e}")
            return False
