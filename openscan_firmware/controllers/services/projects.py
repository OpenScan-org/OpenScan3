"""
Project Manager

The Project Manager is responsible for loading and saving projects to disk.

A project is a directory in the projects folder. Projects have a json file
called openscan_project.json which contains a list of scans and other metadata.
Each project has a directory for each scan.

A project is a collection of scans. The Scans are indexed with scan01, scan02, etc.
and are in the respective projects folder. A scan consists of a collection of photos
and a scan settings json file.

If a scan is running, the Scan Manager sends photos to the Project Manager, which is
responsible for naming them and saving them to disk in the scan folder.

"""

import logging
from datetime import datetime
import io
import asyncio
from fileinput import filename
from typing import Optional

import aiofiles
import pathlib
from tempfile import TemporaryFile
import tempfile
from typing import IO
from zipfile import ZipFile, BadZipFile
import json
import os
import shutil
from io import BytesIO
import numpy as np

from openscan_firmware.models.project import Project
from openscan_firmware.models.scan import Scan
from openscan_firmware.models.camera import PhotoData
from openscan_firmware.controllers.hardware.cameras.camera import CameraController
from openscan_firmware.config.scan import ScanSetting
from openscan_firmware.models.task import TaskStatus


logger = logging.getLogger(__name__)


def _get_project_path(projects_path: str, project_name: str) -> str:
    """Get the absolute path for a project"""
    return os.path.join(str(projects_path), project_name)


def get_project(projects_path: str, project_name: str) -> Project:
    """Load a project from disk including all scan data"""
    project_path = _get_project_path(projects_path, project_name)
    project_json = os.path.join(project_path, "openscan_project.json")

    if not os.path.exists(project_json):
        logger.error(f"Project {project_name} not found")
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
                logger.error(f"Error: Could not load scan {scan_id}: {e}")

    # Create project with string path
    return Project(
        name=project_name,
        path=project_path,
        created=project_data.get("created"),
        uploaded=project_data.get("uploaded", False),
        cloud_project_name=project_data.get("cloud_project_name"),
        downloaded=project_data.get("downloaded", False),
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
        logger.error(f"Error deleting project: {e}", exc_info=e)
        return False


async def _save_scan_json_async(projects_path: str, scan: Scan) -> None:
    """Save a scan to a separate JSON file in the scan directory."""
    scan_json_data = scan.model_dump_json(indent=2)
    await asyncio.to_thread(
        _write_json_atomic,
        _build_scan_file_path(projects_path, scan.index),
        scan_json_data,
    )


def _save_scan_json(projects_path: str, scan: Scan) -> None:
    """Synchronously save a scan to a separate JSON file in the scan directory."""
    scan_json_data = scan.model_dump_json(indent=2)
    scan_file_path = _build_scan_file_path(projects_path, scan.index)
    _write_json_atomic(scan_file_path, scan_json_data)


def _build_scan_file_path(projects_path: str, scan_index: int) -> str:
    """Return the full path to the scan.json for a given scan index."""
    scan_folder_path = os.path.join(projects_path, f"scan{scan_index:02d}")
    os.makedirs(scan_folder_path, exist_ok=True)
    return os.path.join(scan_folder_path, "scan.json")


def _write_json_atomic(file_path: str, payload: str) -> None:
    """Write JSON payload to file atomically using a temporary file."""
    directory = os.path.dirname(file_path)
    os.makedirs(directory, exist_ok=True)

    fd, temp_path = tempfile.mkstemp(dir=directory, prefix=".tmp_", suffix=".json")
    try:
        with os.fdopen(fd, "w") as temp_file:
            temp_file.write(payload)
        os.replace(temp_path, file_path)
    except Exception:
        try:
            os.remove(temp_path)
        except FileNotFoundError:
            pass
        raise


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

    return Scan.model_validate_json(scan_json_data)


def save_project(project: Project):
    """Save the project metadata to openscan_project.json and each scan to its individual file."""
    project_json_path = os.path.join(project.path, "openscan_project.json")

    # Prepare the main project data for openscan_project.json
    # Exclude 'scans' as we'll handle its summary representation manually.
    project_main_data = project.model_dump(mode='json', exclude={'scans'})

    # Create a summary for the scans to be stored in openscan_project.json
    # This summary typically includes just the index or other minimal identifying info.
    scans_summary = {}
    for scan_id, scan_obj in project.scans.items():
        scans_summary[scan_id] = {
            "index": scan_obj.index,
            "created": scan_obj.created.isoformat(),
            "total_size_bytes": scan_obj.total_size_bytes,
        }

    project_main_data['scans'] = scans_summary

    os.makedirs(project.path, exist_ok=True)
    _write_json_atomic(project_json_path, json.dumps(project_main_data, indent=2))

    # Save each scan to its individual JSON file using the refactored _save_scan_json
    for scan in project.scans.values():
        _save_scan_json(project.path, scan)

async def _save_photo_async(photo_data: PhotoData, photo_path: str) -> str:
    """Save a photo to disk and return the final path including extension."""
    handlers = {
        "jpeg": (_save_photo_jpeg, ".jpg"),
        "dng": (_save_photo_dng, ".dng"),
        "rgb_array": (_save_photo_rgb, ".npy"),
        "yuv_array": (_save_photo_yuv, ".npy"),
    }

    try:
        saver, extension = handlers[photo_data.format]
    except KeyError as exc:
        raise ValueError(f"Can't save photo, unsupported image format: {photo_data.format}") from exc

    final_path = f"{photo_path}{extension}"
    await saver(photo_data, final_path)
    logger.info("Saved %s to %s", photo_data.format, final_path)
    return final_path

async def _save_photo_jpeg(photo_data: PhotoData, file_path: str):
    """Save a JPEG photo to a file.

    Args:
        photo_data (PhotoData): The photo data to save.
        file_path (str): The path to save the photo to.
    """
    photo_data.data.seek(0)
    async with aiofiles.open(file_path, 'wb') as f:
        await f.write(photo_data.data.read())

async def _save_photo_dng(photo_data: PhotoData, file_path: str, chunk_size: int = 1024 * 1024):
    """Save a DNG photo to a file.

    We write the dng in chunks to avoid memory issues.

    Args:
        photo_data (PhotoData): The photo data to save.
        file_path (str): The path to save the photo to.
        chunk_size (int, optional): The size of the chunks to write. Defaults to 1024 * 1024.
    """
    photo_data.data.seek(0)
    async with aiofiles.open(file_path, 'wb') as f:
        while chunk := photo_data.data.read(chunk_size):
            await f.write(chunk)

async def _save_numpy_array(array: np.ndarray, file_path: str):
    """Save a numpy array to a file using asyncio.to_thread to avoid blocking the event loop.

    Args:
        array (np.ndarray): The numpy array to save.
        file_path (str): The path to save the numpy array to."""
    await asyncio.to_thread(np.save, file_path, array)

async def _save_photo_rgb(photo_data: PhotoData, file_path: str):
    """Save a rgb numpy array to a file."""
    await _save_numpy_array(photo_data.data, file_path)

async def _save_photo_yuv(photo_data: PhotoData, file_path: str):
    """Save a yuv numpy array to a file."""
    await _save_numpy_array(photo_data.data, file_path)

async def _save_photo_metadata_async(photo_data: PhotoData, file_path: str):
    """Save a photo metadata to a file."""
    async with aiofiles.open(file_path + ".json", 'w') as f:
        await f.write(photo_data.model_dump_json(indent=2, exclude={"data"}))


class ProjectManager:
    def __init__(self, path=pathlib.PurePath("projects")):
        """Initialize project manager with base path"""
        self._path = str(path)
        if not os.path.exists(self._path):
            os.makedirs(self._path)
        self._projects = {}

        logger.debug(f"Initializing ProjectManager at {self._path}")

        # Load existing projects
        for folder in os.listdir(self._path):
            project_json = os.path.join(self._path, folder, "openscan_project.json")
            if os.path.isdir(os.path.join(self._path, folder)) and os.path.exists(project_json):
                try:
                    project = get_project(self._path, folder)
                    self._reset_incomplete_scans(project)
                    self._ensure_scan_sizes(project)
                    self._projects[folder] = project
                except Exception as e:
                    logger.error(f"Error loading project {folder}: {e}", exc_info=True)

        logger.info(f"Loaded {len(self._projects)} projects.")

    def _reset_incomplete_scans(self, project: Project) -> None:
        """Reset scans that were left in a transient state during startup."""
        for scan in project.scans.values():
            if scan.status in {TaskStatus.RUNNING, TaskStatus.PENDING}:
                logger.debug(f"Resetting scan status {scan.index} for project {project.name}")
                scan.status = TaskStatus.INTERRUPTED
                scan.last_updated = datetime.now()
                _save_scan_json(project.path, scan)

    def _ensure_scan_sizes(self, project: Project) -> None:
        """Recalculate scan sizes to keep metadata in sync with disk state."""
        dirty = False
        for scan in project.scans.values():
            recalculated = self._calculate_scan_size_bytes(project, scan)
            if recalculated != scan.total_size_bytes:
                scan.total_size_bytes = recalculated
                scan.last_updated = datetime.now()
                dirty = True

        if dirty:
            save_project(project)

    def _get_scan_directory(self, project: Project, scan: Scan) -> str:
        return os.path.join(project.path, f"scan{scan.index:02d}")

    def _calculate_scan_size_bytes(self, project: Project, scan: Scan) -> int:
        scan_dir = self._get_scan_directory(project, scan)
        if not os.path.exists(scan_dir):
            return 0

        base_size = 0
        for root, _, files in os.walk(scan_dir):
            for filename in files:
                file_path = os.path.join(root, filename)
                if filename == "scan.json":
                    continue
                try:
                    base_size += os.path.getsize(file_path)
                except FileNotFoundError:
                    continue

        def serialized_scan_size(total_size: int) -> int:
            payload = scan.model_copy(update={"total_size_bytes": total_size}).model_dump_json(indent=2)
            return base_size + len(payload.encode("utf-8"))

        target_size = base_size
        for _ in range(5):
            new_size = serialized_scan_size(target_size)
            if new_size == target_size:
                return new_size
            target_size = new_size

        logger.warning(
            "Scan size calculation did not converge for %s/%s, returning last estimate",
            project.name,
            scan.index,
        )
        return target_size

    def _recalculate_and_save_scan_size(self, project_name: str, scan_index: int) -> None:
        project = self.get_project_by_name(project_name)
        if project is None:
            logger.error("Cannot recalculate size, project %s not found", project_name)
            return

        scan_id = f"scan{scan_index:02d}"
        scan = project.scans.get(scan_id)
        if scan is None:
            logger.error("Cannot recalculate size, scan %s missing in project %s", scan_id, project_name)
            return

        scan.total_size_bytes = self._calculate_scan_size_bytes(project, scan)
        scan.last_updated = datetime.now()
        save_project(project)

    def get_project_by_name(self, project_name: str) -> Optional[Project]:
        """Get a project by name. Returns None if the project does not exist."""
        if project_name not in self._projects:
            logger.error(f"Project {project_name} does not exist")
            return None
        return self._projects[project_name]

    def get_all_projects(self) -> dict[str, Project]:
        """Get all projects as a dictionary of project name to a project object"""
        return self._projects

    def mark_uploaded(
        self,
        project_name: str,
        uploaded: bool = True,
        cloud_project_name: str | None = None,
    ) -> Project:
        """Set the uploaded flag for a project and persist the change.

        Args:
            project_name: Name of the project to update.
            uploaded: New uploaded state (defaults to True).
            cloud_project_name: Optional remote project identifier to store.

        Returns:
            Updated Project instance.

        Raises:
            ValueError: If the project does not exist.
        """

        project = self.get_project_by_name(project_name)
        if project is None:
            raise ValueError(f"Project {project_name} does not exist")

        project.uploaded = uploaded
        if cloud_project_name is not None:
            project.cloud_project_name = cloud_project_name
        elif not uploaded:
            project.cloud_project_name = None
        save_project(project)
        return project
        
    def mark_downloaded(self, project_name: str, downloaded: bool = True) -> Project:
        """Set the downloaded flag for a project and persist the change.

        Args:
            project_name: Name of the project to update.
            downloaded: New downloaded state (defaults to True).

        Returns:
            Updated Project instance.

        Raises:
            ValueError: If the project does not exist.
        """

        project = self.get_project_by_name(project_name)
        if project is None:
            raise ValueError(f"Project {project_name} does not exist")

        project.downloaded = downloaded
        save_project(project)
        logger.info("Set downloaded=%s for project %s", downloaded, project_name)
        return project

    def add_download(self, project_name: str, archive_path: str) -> Project:
        """Install a reconstruction archive into the project's ``model/`` directory.

        Args:
            project_name: Name of the project that should receive the model.
            archive_path: Path to the ZIP archive that should be unpacked.

        Returns:
            Updated project instance with ``downloaded`` set to ``True``.

        Raises:
            ValueError: If the project does not exist or the archive is invalid.
            FileNotFoundError: If the archive does not exist.
        """

        project = self.get_project_by_name(project_name)
        if project is None:
            raise ValueError(f"Project {project_name} does not exist")

        archive = pathlib.Path(archive_path)
        if not archive.exists():
            raise FileNotFoundError(f"Archive not found: {archive}")
        if archive.is_dir():
            raise ValueError(f"Archive path must point to a file: {archive}")

        model_dir = pathlib.Path(project.path) / "model"

        if project.downloaded:
            logger.warning(
                "Overwriting previously downloaded model for project %s", project_name
            )

        try:
            with ZipFile(archive, "r") as zip_file:
                for member in zip_file.namelist():
                    member_path = pathlib.Path(member)
                    if member_path.is_absolute() or ".." in member_path.parts:
                        raise ValueError(
                            f"Archive contains unsupported path entry: {member}"
                        )

                if model_dir.exists():
                    contents = list(model_dir.iterdir())
                    if contents:
                        logger.warning(
                            "Clearing existing contents of model/ for project %s",
                            project_name,
                        )
                    shutil.rmtree(model_dir)

                model_dir.mkdir(parents=True, exist_ok=True)
                zip_file.extractall(model_dir)
        except BadZipFile as exc:
            raise ValueError(f"Invalid zip archive: {archive}") from exc

        logger.info(
            "Installed reconstruction archive %s into %s", archive, model_dir
        )

        return self.mark_downloaded(project_name, True)


    def add_project(self, name: str, project_description=None) -> Project:
        """Create a new project.

        Args:
            name: The name of the project.
            project_description: Optional description for the project.

        Returns:
            The newly created project.
        """
        if name in self._projects.keys():
            logger.error(f"Project {name} already exists")
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

        logger.debug(f"Created project {name} with description {project_description}")

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
            logger.info(f"Deleted project {project.name}")
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
            logger.error(f"Project {project_name} does not exist")
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
            camera_name=camera_controller.camera.name,
            camera_settings=camera_controller.settings.model,
        )


        project.scans[f"scan{new_index:02d}"] = scan

        loop = asyncio.get_running_loop()
        #await loop.run_in_executor(None, save_project, project)
        save_project(project)

        logger.info(f"Added scan {scan.index} to project {project.name}")

        return scan

    async def add_photo_async(self, photo_data: PhotoData) -> None:
        """Asynchronously save a photo and its metadata to the corresponding project

        Args:
            photo_data: The photo data to save.
        """

        project = self.get_project_by_name(photo_data.scan_metadata.project_name)

        photo_dir, photo_filename = self._prepare_photo_path(photo_data)
        metadata_dir = os.path.join(photo_dir, "metadata")
        os.makedirs(metadata_dir, exist_ok=True)

        saved_photo_path, _ = await asyncio.gather(
            _save_photo_async(photo_data, os.path.join(photo_dir, photo_filename)),
            _save_photo_metadata_async(photo_data, os.path.join(metadata_dir, photo_filename))
        )

        logger.info(f"Saved photo {photo_filename} and metadata to {photo_dir}")

        saved_filename = os.path.basename(saved_photo_path)

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            self._register_photo_file,
            photo_data.scan_metadata.project_name,
            photo_data.scan_metadata.scan_index,
            saved_filename,
        )

        await loop.run_in_executor(
            None,
            self._recalculate_and_save_scan_size,
            photo_data.scan_metadata.project_name,
            photo_data.scan_metadata.scan_index,
        )

    def _prepare_photo_path(self, photo_data: PhotoData) -> tuple[str, str]:
        """Prepare the path and the filename for a photo file.

        Filenames are e.g. scan01_001.jpg or, if focus stacking is enabled, scan01_001_fs01.jpg

        Args:
            photo_data: The photo data to prepare the path for.

        Returns:
            Tuple of (photo directory, photo filename)
        """
        project = self.get_project_by_name(photo_data.scan_metadata.project_name)
        photo_dir = os.path.join(project.path, f"scan{photo_data.scan_metadata.scan_index:02d}")

        scan_index = photo_data.scan_metadata.scan_index
        position = photo_data.scan_metadata.step

        photo_filename = f"scan{scan_index:02d}_{position:03d}"

        if photo_data.scan_metadata.stack_index is not None:
            focus_stack_index = photo_data.scan_metadata.stack_index
            photo_filename = photo_filename + f"_fs{focus_stack_index:02d}"

        return photo_dir, photo_filename

    async def save_scan_state(self, scan: Scan) -> None:
        """Persist the latest scan state without reloading the project from disk."""
        project = self.get_project_by_name(scan.project_name)
        if project is None:
            # Fall back to loading from disk to avoid data loss in exceptional cases.
            project = get_project(self._path, scan.project_name)
            self._projects[scan.project_name] = project

        scan_id = f"scan{scan.index:02d}"
        project.scans[scan_id] = scan

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _save_scan_json, project.path, scan)


    def get_scan_by_index(self, project_name: str, scan_index: int) -> Optional[Scan]:
        """Get a scan by its index from a project

        Args:
            project_name: The name of the project to get the scan from.
            scan_index: The index of the scan to get.

        Returns:
             The scan object with the given index if it exists, None if not
        """
        try:
            project = self._projects[project_name]
        except KeyError:
            logger.error("Project %s not found", project_name)
            return None

        scan_id = f"scan{scan_index:02d}"
        scan = project.scans.get(scan_id)
        if scan is None:
            logger.error("Scan %s not found in project %s", scan_id, project_name)
            return None

        return scan

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

            logger.info(f"Deleted scan {scan_id} from project {scan.project_name}")

            return True
        except Exception as e:
            logger.error(f"Error deleting scan: {e}", exc_info=True)
            return False

    def delete_photos(self, scan: Scan, photo_filenames: list[str]) -> bool:
        """Delete one or more photos from a scan in a project"""
        try:
            project = self._projects[scan.project_name]
            scan_id = f"scan{scan.index:02d}"
            scan_dir = os.path.join(project.path, scan_id)

            for photo_filename in photo_filenames:
                if photo_filename != os.path.basename(photo_filename):
                    raise ValueError("Photo filename must not contain directories")

                file_path = os.path.join(scan_dir, photo_filename)
                if os.path.exists(file_path):
                    os.remove(file_path)

                metadata_name = os.path.splitext(photo_filename)[0] + ".json"
                metadata_path = os.path.join(scan_dir, "metadata", metadata_name)
                if os.path.exists(metadata_path):
                    os.remove(metadata_path)

                self._remove_photo_file_record(project, scan, photo_filename)

            self._recalculate_and_save_scan_size(scan.project_name, scan.index)

            logger.info(
                "Deleted photos %s from scan %s in project %s",
                photo_filenames,
                scan_id,
                scan.project_name,
            )

            return True
        except Exception as e:
            logger.error(f"Error deleting photo: {e}", exc_info=True)
            return False

    def _register_photo_file(self, project_name: str, scan_index: int, filename: str) -> None:
        project = self.get_project_by_name(project_name)
        if project is None:
            raise ValueError(f"Project {project_name} does not exist")

        scan_id = f"scan{scan_index:02d}"
        scan = project.scans.get(scan_id)
        if scan is None:
            raise ValueError(f"Scan {scan_index} not found in project {project_name}")

        if filename not in scan.photos:
            scan.photos.append(filename)
            scan.last_updated = datetime.now()
            _save_scan_json(project.path, scan)

    def _remove_photo_file_record(self, project: Project, scan: Scan, filename: str) -> None:
        if filename in scan.photos:
            scan.photos.remove(filename)
            scan.last_updated = datetime.now()
            _save_scan_json(project.path, scan)

    def get_photo_file(self, project_name: str, scan_index: int, filename: str) -> tuple[Scan, str, dict | None]:
        """Return scan, absolute photo path, and optional metadata for a stored photo."""
        if not filename or filename != os.path.basename(filename):
            raise ValueError("Invalid photo filename")

        project = self.get_project_by_name(project_name)
        if project is None:
            raise FileNotFoundError(f"Project {project_name} not found")

        scan_id = f"scan{scan_index:02d}"
        scan = project.scans.get(scan_id)
        if scan is None:
            raise FileNotFoundError(f"Scan {scan_index} not found in project {project_name}")

        scan_dir = self._get_scan_directory(project, scan)
        photo_path = os.path.join(scan_dir, filename)
        if not os.path.exists(photo_path):
            raise FileNotFoundError(f"Photo {filename} not found")

        metadata = None
        metadata_name = os.path.splitext(filename)[0] + ".json"
        metadata_path = os.path.join(scan_dir, "metadata", metadata_name)
        if os.path.exists(metadata_path):
            with open(metadata_path, "r", encoding="utf-8") as handle:
                metadata = json.load(handle)

        return scan, photo_path, metadata


_active_project_manager: Optional[ProjectManager] = None

def get_project_manager(path: Optional[pathlib.PurePath] = None) -> ProjectManager:
    """Get or create a ProjectManager instance for the given path"""
    global _active_project_manager

    if path is None:
        if _active_project_manager:
            logger.debug("No path provided, returning existing ProjectManager")
            return _active_project_manager

    resolved_path_str = str(pathlib.Path(path).resolve()) if path else None

    if _active_project_manager is None:
        if resolved_path_str is None:
            logger.warning("No path provided, initializing new ProjectManager with default path")
            _active_project_manager = ProjectManager()
        logger.info(f"Creating new ProjectManager for {path}")
        _active_project_manager = ProjectManager(path)
        return _active_project_manager
    else:
        # An instance already exists, check if paths match
        # Ensure _active_project_manager._path is also a resolved string for fair comparison
        # Assuming _active_project_manager._path was stored as a resolved string or Path object
        current_manager_path_str = str(pathlib.Path(_active_project_manager._path).resolve())

        if resolved_path_str == current_manager_path_str:
            logger.debug("Explicitly requested ProjectManager for the same path already exists, returning existing ProjectManager")
            return _active_project_manager
        else:
            raise RuntimeError(
                f"ProjectManager is already initialized with a different path. "
                f"Current: '{current_manager_path_str}', Requested: '{resolved_path_str}'"
            )