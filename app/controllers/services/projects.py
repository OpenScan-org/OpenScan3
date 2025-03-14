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
import orjson
import os
import shutil
from io import BytesIO

from gpg.version import description

from app.models.project import Project
from app.models.scan import Scan, ScanStatus
from app.models.camera import Camera
from controllers.hardware.cameras.camera import CameraControllerFactory
from app.config import config
from config.scan import ScanSetting
from app.models.paths import PathMethod

ALLOWED_EXTENSIONS = (".jpg", ".jpeg", ".png")


def get_projects() -> list[Project]:
    """Get all projects in the projects directory"""
    projects = []
    for folder in os.listdir(config.projects_path):
        project_json = os.path.join(config.projects_path, folder, "openscan_project.json")
        if os.path.exists(project_json):
            try:
                projects.append(get_project(folder))
            except Exception as e:
                print(f"Error loading project {folder}: {e}")
    return projects


def _get_project_path(project_name: str) -> str:
    """Get the absolute path for a project"""
    return os.path.join(str(config.projects_path), project_name)


def _get_project_photos(project_path: str) -> list[str]:
    """Get list of photos in a project directory"""
    return [
        file for file in os.listdir(project_path)
        if file.lower().endswith(ALLOWED_EXTENSIONS)
    ]


def get_project(project_name: str) -> Project:
    """Load a project from disk including all scan data"""
    project_path = _get_project_path(project_name)
    project_json = os.path.join(project_path, "openscan_project.json")

    if not os.path.exists(project_json):
        raise FileNotFoundError(f"Project {project_name} not found")

    # Load project data
    with open(project_json, "rb") as f:
        project_data = orjson.loads(f.read())

    # Load scan data from their respective folders
    scans = {}
    if "scans" in project_data:
        for scan_id, scan_summary in project_data["scans"].items():
            try:
                scan = _load_scan_json(project_name, scan_summary["index"])
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
    """Delete a project from disk"""
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

def _serialize_scan_setting(scan_setting: ScanSetting) -> dict:
    """Convert ScanSetting to a serializable dictionary"""
    return {
        "path_method": scan_setting.path_method.value,  # Enum to string
        "points": scan_setting.points,
        "focus_stacks": scan_setting.focus_stacks,
        "focus_range": list(scan_setting.focus_range),  # Tuple to list
    }


def _deserialize_scan_settings(settings_data: dict) -> ScanSetting:
    """Convert a serialized settings dictionary back to a ScanSetting object"""
    # Convert path method back to an Enum
    path_method = PathMethod(settings_data["path_method"])

    # Convert focus_range to tuple
    focus_range = tuple(settings_data.get("focus_range", (0.0, 0.0)))

    # Recreate ScanSettings
    return ScanSetting(
        path_method=path_method,
        points=settings_data["points"],
        focus_stacks=settings_data.get("focus_stacks", 1),
        focus_range=focus_range
    )


def _save_scan_json(scan: Scan) -> None:
    """Save a scan to a separate JSON file in the scan directory"""
    # Create a new folder if necessary
    scan_path = os.path.join(scan.project_name, f"scan{scan.index:02d}")
    os.makedirs(scan_path, exist_ok=True)

    # Serialize Scan data
    serialized_scan = {
        "project_name": scan.project_name,
        "index": scan.index,
        "created": scan.created,
        "description": scan.description if scan.description else None,

        "status": scan.status.value,
        "current_step": scan.current_step,
        "system_message": scan.system_message,
        "last_updated": scan.last_updated.isoformat() if scan.last_updated else None,
        "duration": scan.duration,

        "photos": scan.photos,
    }

    # ScanSetting
    if scan.settings:
        serialized_scan["settings"] = _serialize_scan_setting(scan.settings)

    # CameraSettings
    if scan.camera_settings:
        serialized_scan["camera_settings"] = scan.camera_settings.__dict__

    # Save scan settings in a json file
    with open(os.path.join(scan_path, "scan.json"), "wb") as f:
        f.write(orjson.dumps(serialized_scan))


def _load_scan_json(project_name: str, scan_index: int) -> Scan:
    """Load a scan from its JSON file"""
    scan_path = os.path.join(project_name, f"scan{scan_index:02d}")
    scan_file = os.path.join(scan_path, "scan.json")

    if not os.path.exists(scan_file):
        raise FileNotFoundError(f"Scan file not found: {scan_file}")

    with open(scan_file, "rb") as f:
        scan_data = orjson.loads(f.read())

    # Reconstruct the ScanSettings
    settings = None
    if "settings" in scan_data:
        settings = _deserialize_scan_settings(scan_data["settings"])

    # Reconstruct the CameraSettings
    camera_settings = {}
    if "camera_settings" in scan_data:
        camera_settings = scan_data["camera_settings"]

    # Recreate the Scan object
    return Scan(
        project_name=scan_data["project_name"],
        index=scan_data["index"],
        created=scan_data["created"],
        description=scan_data.get("description", None),

        settings=settings,
        camera_settings=camera_settings,

        status=ScanStatus(scan_data.get("status", "pending")),
        current_step=scan_data.get("current_step", 0),
        system_message=scan_data.get("error_message"),
        last_updated=datetime.fromisoformat(scan_data["last_updated"]) if scan_data.get("last_updated") else None,

        duration=scan_data.get("duration", 0.0),
        photos=scan_data.get("photos", [])
    )


def save_project(project: Project):
    """Save a project and scans"""
    # Ensure project directory exists
    project.create_directory()

    # Save scan data separately
    scan_summaries = {}
    if project.scans:
        for scan_id, scan in project.scans.items():
            # save scan data in the corresponding folders
            _save_scan_json(scan)

            # save only scan summaries
            scan_summaries[scan_id] = {
                "index": scan.index,
                "created": scan.created,
                "status": scan.status.value
            }

    # Save project data and scan summaries
    project_json = os.path.join(project.path, "openscan_project.json")
    with open(project_json, "wb") as f:
        f.write(orjson.dumps({
            "name": project.name,
            "created": project.created,
            "uploaded": project.uploaded,
            "scans": scan_summaries
        }))

    for scan in project.scans.values():
        _save_scan_json(scan)


def new_project(project_name: str) -> Project:
    projects = get_projects()
    if project_name in [project.name for project in projects]:
        raise ValueError(f"Project {project_name} already exists")
    project_path = _get_project_path(project_name)
    project = Project(name=project_name, path=project_path, created=datetime.now(), scans={})
    save_project(project)
    return project


def compress_project_photos(project: Project) -> IO[bytes]:
    file = TemporaryFile()
    with ZipFile(file, "w") as zipf:
        counter = 1
        for photo in project.photos:
            zipf.write(project.path.joinpath(photo), photo)
            print(f"{photo} - {counter}/{len(project.photos)}")
            counter += 1
    return file


def split_file(file: IO[bytes]) -> list[io.BytesIO]:
    file.seek(0, 2)
    file.seek(0)

    chunk = file.read(config.cloud.split_size)
    while chunk:
        yield io.BytesIO(chunk)
        chunk = file.read(config.cloud.split_size)


class ProjectManager:
    def __init__(self, path=config.projects_path):
        """Initialize project manager with base path"""
        self._path = str(path)  # Ensure string path
        self._projects = {}
        self._current_project = None

        # Load existing projects
        for folder in os.listdir(self._path):
            project_json = os.path.join(self._path, folder, "openscan_project.json")
            if os.path.isdir(os.path.join(self._path, folder)) and os.path.exists(project_json):
                try:
                    self._projects[folder] = get_project(folder)
                except Exception as e:
                    print(f"Error loading project {folder}: {e}")

        # Reset any running scans to failed state (app was restarted)
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
        if project_name not in self._projects:
            print(f"Project {project_name} does not exist")
            return None
        return self._projects[project_name]

    def get_all_projects(self) -> dict[str, Project]:
        return self._projects

    def get_current_project(self) -> Project:
        return self._current_project

    def set_current_project(self, project: Project) -> bool:
        if project.name not in self._projects:
            raise ValueError(f"Project {project.name} does not exist")
        if self._current_project and self._current_project.name != project.name:
            save_project(project)
        self._current_project = self._projects[project.name]
        return True

    def add_project(self, name: str, project_description=None) -> Project:
        """Create a new project"""
        if name in self._projects.keys():
            raise ValueError(f"Project {name} already exists")

        project_path = _get_project_path(name)

        # Create project object (will validate name etc.)
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
        self.set_current_project(project)

        return project


    def delete_project(self, project: Project) -> bool:
        """Delete a project from the filesystem"""
        if delete_project(project):
            del self._projects[project.name]
            return True
        return False

    def add_scan(self, project_name: str, camera: Camera, scan_settings: ScanSetting) -> Optional[Scan]:
        """Add a new scan to a project"""
        if not self.set_current_project(self._projects[project_name]):
            return None

        if self._current_project.scans:
            sorted_scans = sorted(self._current_project.scans.values(), key=lambda scan: scan.index)
            new_index = sorted_scans[-1].index + 1
        else:
            new_index = 1

        os.makedirs(os.path.join(self._current_project.path, f"scan{new_index:02d}"), exist_ok=True)

        camera_controller = CameraControllerFactory.get_controller(camera)

        scan = Scan(
            project_name=self._current_project.name,
            index=new_index,
            created=datetime.now(),
            settings=scan_settings,
            camera_settings=camera_controller.get_all_settings()
        )


        self._current_project.scans[f"scan{new_index:02d}"] = scan

        return scan

    async def add_photo_async(self, scan: Scan, photo, photo_info: dict) -> None:
        """Asynchronously save a photo to the current project """
        photo = BytesIO(photo)
        photo.seek(0)
        photo_data = photo.read()

        self.set_current_project(self._projects[scan.project_name])
        project = self._current_project

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

        if len(scan.photos) == scan.settings.points:
            scan.finished = True

        save_project(project)

    @staticmethod
    def save(self, scan: Scan):
        project = get_project(scan.project_name)
        save_project(project)


    def get_scan_by_index(self, project_name: str, scan_index: int) -> Optional[Scan]:
        """Get a scan by its index from a project"""
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

    def delete_scan(self, project: Project, scan: Scan) -> bool:
        """Delete a scan from a project"""
        try:
            # Check if project exists
            if project.name not in self._projects:
                print(f"Project {project.name} does not exist")
                return False

            # Check if scan exists
            scan_id = f"scan{scan.index:02d}"
            if scan_id not in project.scans:
                print(f"Scan {scan_id} does not exist in project {project.name}")
                return False

            # Delete scan directory and remove scan from project
            scan_path = os.path.join(project.path, scan_id)
            if os.path.exists(scan_path):
                shutil.rmtree(scan_path)
            del project.scans[scan_id]

            save_project(project)

            return True
        except Exception as e:
            print(f"Error deleting scan: {e}")
            return False

    def delete_photo(self, project: Project, scan: Scan, photo_name: str) -> bool:
        pass
