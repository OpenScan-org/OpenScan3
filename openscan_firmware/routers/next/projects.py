from fastapi import APIRouter, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
import pathlib
from typing import Optional, List, Any
import asyncio
import os
import json
import mimetypes
from datetime import datetime
import logging


from openscan_firmware.controllers.hardware.cameras.camera import get_all_camera_controllers, get_camera_controller
from openscan_firmware.controllers.services import projects, cloud
import openscan_firmware.controllers.services.scans as scans #import start_scan, cancel_scan, pause_scan, resume_scan
from openscan_firmware.models.project import Project
from openscan_firmware.config.scan import ScanSetting
from openscan_firmware.models.scan import Scan
from openscan_firmware.models.task import Task, TaskStatus

from openscan_firmware.controllers.services.projects import get_project_manager
from openscan_firmware.controllers.services.tasks.task_manager import task_manager, get_task_manager

router = APIRouter(
    prefix="/projects",
    tags=["projects"],
    responses={404: {"description": "Not found"}},
)

logger = logging.getLogger(__name__)

class DeleteResponse(BaseModel):
    success: bool
    message: str
    deleted: list[str]


class PhotoResponse(BaseModel):
    project_name: str
    scan_index: int
    filename: str
    content_type: str
    size_bytes: int
    metadata: Optional[dict[str, Any]] = None
    photo_data: bytes


@router.get("/", response_model=dict[str, Project])
async def get_projects():
    """Get all projects with serialized data

    Returns:
        dict[str, Project]: A dictionary of project name to a project object
    """
    project_manager = get_project_manager()
    projects_dict = project_manager.get_all_projects()
    return projects_dict

@router.get("/{project_name}", response_model=Project)
async def get_project(project_name: str):
    """Get a project

    Args:
        project_name: The name of the project to get

    Returns:
        Project: The project object if found, None if not
    """
    project_manager = get_project_manager()
    project = project_manager.get_project_by_name(project_name)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project {project_name} not found")
    return project


@router.post("/{project_name}", response_model=Project)
async def new_project(project_name: str, project_description: Optional[str] = ""):
    """Create a new project

    Args:
        project_name: The name of the project to create
        project_description: Optional description for the project

    Returns:
        Project: The newly created project if successful, None if not
    """
    try:
        project_manager = get_project_manager()
        return project_manager.add_project(project_name, project_description)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Project {project_name} already exists.")


@router.post("/{project_name}/scan", response_model=Task)
async def add_scan_with_description(project_name: str,
                   camera_name: str,
                   scan_settings: ScanSetting,
                   scan_description:  Optional[str] = "") -> Task:
    """Add a new scan to a project and return the created Task

    Args:
        project_name: The name of the project to add the scan to
        camera_name: The name of the camera to use for the scan
        scan_settings: The settings for the scan
        scan_description: Optional description for the scan

    Returns:
        Task: The Task representing the started scan
    """
    camera_controller = get_camera_controller(camera_name)
    project_manager = get_project_manager()

    try:
        scan = project_manager.add_scan(project_name, camera_controller, scan_settings, scan_description)
        task = await scans.start_scan(project_manager, scan, camera_controller)
        return task

    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start scan: {e}")



# Cloud uploads --------------------------------------------------------------


@router.post("/{project_name}/upload", response_model=Task)
async def upload_project_to_cloud(project_name: str, token_override: Optional[str] = None) -> Task:
    """Schedule an asynchronous cloud upload for a project.

    Args:
        project_name: The name of the project
        token_override: Optional token override

    Returns:
        Task: The TaskManager model describing the scheduled upload
    """
    try:
        task = await cloud.upload_project(project_name, token=token_override)
    except cloud.CloudServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return task


@router.post("/{project_name}/download", response_model=Task)
async def download_project_from_cloud(
    project_name: str,
    token_override: Optional[str] = None,
    remote_project: Optional[str] = None,
) -> Task:
    """Schedule an asynchronous cloud download for a project's reconstruction.

    Args:
        project_name: The name of the project
        token_override: Optional token override
        remote_project: Optional explicit remote project name, defaults to the stored cloud name

    Returns:
        Task: The TaskManager model describing the scheduled download
    """
    try:
        task = await cloud.download_project(
            project_name,
            token=token_override,
            remote_project=remote_project,
        )
    except cloud.CloudServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return task


@router.delete("/{project_name}/{scan_index}/photos", response_model=DeleteResponse)
async def delete_photos(project_name: str, scan_index: int, photo_filenames: list[str]):
    """Delete photos from a scan in a project

    Args:
        project_name: The name of the project
        scan_index: The index of the scan
        photo_filenames: A list of photo filenames to delete

    Returns:
        True if the photos were deleted successfully, False otherwise
    """
    project_manager = get_project_manager()
    try:
        scan = project_manager.get_scan_by_index(project_name, scan_index)
        project_manager.delete_photos(scan, photo_filenames)
        return DeleteResponse(
            success=True,
            message="Photos deleted successfully",
            deleted=photo_filenames
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Project {project_name} not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{project_name}", response_model=DeleteResponse)
async def delete_project(project_name: str):
    """Delete a project

    Args:
        project_name: The name of the project to delete

    Returns:
        DeleteResponse: A response object containing the result of the deletion
    """
    project_manager = get_project_manager()
    project = project_manager.get_project_by_name(project_name)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project {project_name} not found")
    try:
        project_manager.delete_project(project)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return DeleteResponse(
        success=True,
        message="Project deleted successfully",
        deleted=[project_name]
    )


@router.get("/{project_name}/{scan_index:int}/photo", response_model=PhotoResponse)
async def get_scan_photo(
    project_name: str,
    scan_index: int,
    filename: str = Query(..., description="Photo filename including extension, e.g. scan01_001.jpg"),
    file_only: bool = Query(False, description="Return only the raw file instead of JSON payload"),
):
    """Fetch a stored scan photo either as JSON payload or direct file download."""
    project_manager = get_project_manager()
    try:
        scan, photo_path, metadata = project_manager.get_photo_file(project_name, scan_index, filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    content_type, _ = mimetypes.guess_type(photo_path)
    media_type = content_type or "application/octet-stream"

    if file_only:
        return FileResponse(photo_path, media_type=media_type, filename=filename)

    def _read_file_bytes(path: str) -> bytes:
        with open(path, "rb") as handle:
            return handle.read()

    photo_bytes = await asyncio.to_thread(_read_file_bytes, photo_path)

    return PhotoResponse(
        project_name=scan.project_name,
        scan_index=scan.index,
        filename=filename,
        content_type=media_type,
        size_bytes=len(photo_bytes),
        metadata=metadata,
        photo_data=photo_bytes,
    )


@router.get("/{project_name}/scans/{scan_index:int}/path")
async def get_scan_path(project_name: str, scan_index: int):
    project_manager = get_project_manager()
    project = project_manager.get_project_by_name(project_name)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project {project_name} not found")

    scan = project_manager.get_scan_by_index(project_name, scan_index)
    if not scan:
        raise HTTPException(status_code=404, detail=f"Scan {scan_index} not found")

    scan_dir = os.path.join(project.path, f"scan{scan_index:02d}")
    path_file = os.path.join(scan_dir, "path.json")
    if not os.path.exists(path_file):
        raise HTTPException(status_code=404, detail="path.json not found")

    def _read_json(path: str) -> dict:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)

    return await asyncio.to_thread(_read_json, path_file)


@router.delete("/{project_name}/scans/{scan_index}", response_model=DeleteResponse)
async def delete_scan(project_name: str, scan_index: int):
    """Delete a scan from a project

    Args:
        project_name: The name of the project
        scan_index: The index of the scan to delete

    Returns:
        DeleteResponse: Result of the deletion operation
    """
    project_manager = get_project_manager()
    scan = project_manager.get_scan_by_index(project_name, scan_index)
    try:
        project_manager.delete_scan(scan)
        return DeleteResponse(
            success=True,
            message="Scan deleted successfully",
            deleted=[f"{project_name}:scan{scan_index:02d}"]
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Project {project_name} not found")


@router.get("/{project_name}/scans/{scan_index:int}/status", response_model=Task)
async def get_scan_status(project_name: str, scan_index: int):
    """Get the current task for a scan

    Args:
        project_name: The name of the project
        scan_index: The index of the scan to get the status of

    Returns:
        Task: The task representing the scan execution
    """
    try:
        project_manager = get_project_manager()
        scan = project_manager.get_scan_by_index(project_name, scan_index)
        if not scan:
            raise HTTPException(status_code=404, detail=f"Scan {scan_index} not found")
        if not scan.task_id:
            raise HTTPException(status_code=404, detail=f"Scan {scan_index} has no associated task")

        task_manager_instance = get_task_manager()
        task = task_manager_instance.get_task_info(scan.task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"Task {scan.task_id} not found for scan {scan_index}")

        return task
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/{project_name}/scans/{scan_index:int}/pause", response_model=Task)
async def pause_scan(project_name: str, scan_index: int) -> Task:
    """Pause a running scan and return the updated Task

    Args:
        project_name: The name of the project
        scan_index: The index of the scan to pause

    Returns:
        Task: The updated task state
    """
    project_manager = get_project_manager()
    scan = project_manager.get_scan_by_index(project_name, scan_index)
    if not scan:
        raise HTTPException(status_code=404, detail=f"Scan {scan_index} not found")

    task = await scans.pause_scan(scan)
    if task is None:
        raise HTTPException(status_code=409, detail="Scan is not running or cannot be paused.")

    return task


@router.patch("/{project_name}/scans/{scan_index:int}/resume", response_model=Task)
async def resume_scan(project_name: str, scan_index: int, camera_name: str) -> Task:
    """Resume a paused, cancelled or failed scan and return the resulting Task

    Args:
        project_name: The name of the project
        scan_index: The index of the scan to resume
        camera_name: The name of the camera to use for the scan

    Returns:
        Task: The resumed or restarted task
    """
    try:

        camera_controller = get_camera_controller(camera_name)
        project_manager = get_project_manager()
        scan = project_manager.get_scan_by_index(project_name, scan_index)
        if not scan:
            raise HTTPException(status_code=404, detail=f"Scan {scan_index} not found")

        task_manager_instance = get_task_manager()
        existing_task = task_manager_instance.get_task_info(scan.task_id) if scan.task_id else None

        if existing_task and existing_task.status == TaskStatus.PAUSED:
            task = await scans.resume_scan(scan)
        elif not existing_task or existing_task.status in [TaskStatus.COMPLETED, TaskStatus.CANCELLED, TaskStatus.ERROR]:
            task = await scans.start_scan(
                project_manager,
                scan,
                camera_controller,
                start_from_step=scan.current_step
            )
        else:
            raise HTTPException(status_code=409, detail=f"Scan cannot be resumed from its current state: {existing_task.status.value}")

        if task is None:
            raise HTTPException(status_code=409, detail="Failed to resume scan task.")

        return task
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/{project_name}/scans/{scan_index:int}/cancel", response_model=Task)
async def cancel_scan(project_name: str, scan_index: int) -> Task:
    """Cancel a running scan and return the resulting Task

    Args:
        project_name: The name of the project
        scan_index: The index of the scan to cancel

    Returns:
        Task: The updated task state
    """
    project_manager = get_project_manager()
    scan = project_manager.get_scan_by_index(project_name, scan_index)
    if not scan:
        raise HTTPException(status_code=404, detail=f"Scan {scan_index} not found")

    try:
        task = await scans.cancel_scan(scan)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if task is None:
        raise HTTPException(status_code=409, detail="Scan is not running or cannot be cancelled.")

    return task


def _serialize_project_for_zip(project: Project) -> str:
    """Serialize a project to JSON for inclusion in a ZIP file

    Args:
        project: Project to serialize

    Returns:
        str: JSON string representation of the project
    """
    # Use jsonable_encoder to convert the project to a dict
    project_dict = jsonable_encoder(project)

    # Convert to JSON string
    return json.dumps(project_dict, indent=2)


@router.get("/{project_name}/zip")
async def download_project(project_name: str):
    """Download a project as a ZIP file stream

    This endpoint streams the entire project directory as a ZIP file,
    including all scans, photos, and metadata.

    Args:
        project_name: Name of the project to download

    Returns:
        StreamingResponse: ZIP file stream
    """
    try:
        # Import zipstream-ng
        from zipstream import ZipStream
        project_manager = get_project_manager()
        # Get project
        project = project_manager.get_project_by_name(project_name)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project {project_name} not found")

        # Create ZipStream from project path
        zs = ZipStream.from_path(project.path)

        # Add project metadata
        zs.add(_serialize_project_for_zip(project), "project_metadata.json")

        # Return streaming response
        headers = {
            "Content-Disposition": f"attachment; filename={project_name}.zip",
        }
        if getattr(zs, "last_modified", None):
            headers["Last-Modified"] = str(zs.last_modified)

        response = StreamingResponse(
            zs,
            media_type="application/zip",
            headers=headers,
        )

        return response
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Project {project_name} not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{project_name}/scans/zip")
async def download_scans(project_name: str, scan_indices: List[int] = Query(None)):
    """Download selected scans from a project as a ZIP file stream

    This endpoint streams selected scans from a project as a ZIP file.
    If no scan indices are provided, all scans will be included.

    Args:
        project_name: Name of the project
        scan_indices: List of scan indices to include in the ZIP file

    Returns:
        StreamingResponse: ZIP file stream
    """
    try:
        from zipstream import ZipStream
        project_manager = get_project_manager()
        project = project_manager.get_project_by_name(project_name)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project {project_name} not found")

        zs = ZipStream(sized=True)
        zs.comment = f"OpenScan3 Project: {project_name} - Generated on {datetime.now().isoformat()}"

        # Build filename based on what's being downloaded
        if scan_indices:
            if len(scan_indices) == 1:
                filename = f"{project_name}_scan{scan_indices[0]:02d}.zip"
            else:
                scan_nums = "_".join(str(i) for i in sorted(scan_indices))
                filename = f"{project_name}_scans_{scan_nums}.zip"

            for scan_index in scan_indices:
                try:
                    scan = project_manager.get_scan_by_index(project_name, scan_index)
                    if not scan:
                        logger.error(f"Scan with index {scan_index} not found")
                        continue
                    scan_dir = os.path.join(project.path, f"scan{scan_index:02d}")
                    if os.path.exists(scan_dir):
                        zs.add_path(scan_dir, f"scan{scan_index:02d}")
                except Exception as e:
                    logger.error(f"Failed to add scan {scan_index} to zip: {e}")
                    continue
        else:
            filename = f"{project_name}_all_scans.zip"
            for scan_id, scan in project.scans.items():
                scan_dir = os.path.join(project.path, f"scan_{scan.index}")
                if os.path.exists(scan_dir):
                    zs.add_path(scan_dir, f"scan_{scan.index}")

        zs.add(_serialize_project_for_zip(project), "project_metadata.json")

        headers = {
            "Content-Disposition": f"attachment; filename={filename}",
        }
        if getattr(zs, "last_modified", None):
            headers["Last-Modified"] = str(zs.last_modified)

        response = StreamingResponse(
            zs,
            media_type="application/zip",
            headers=headers,
        )
        return response
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"")
    except Exception as e:
        print(e)
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{project_name}/scans/{scan_index:int}", response_model=Scan)
async def get_scan(project_name: str, scan_index: int):
    """Get Scan by project and index

    Args:
        project_name: The name of the project
        scan_index: The index of the scan

    Returns:
        Scan: The scan object
    """
    try:
        project_manager = get_project_manager()
        return project_manager.get_scan_by_index(project_name, scan_index)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
