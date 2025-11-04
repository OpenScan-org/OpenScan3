from fastapi import APIRouter, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import pathlib
from typing import Optional, List
import asyncio
import os
import json
from datetime import datetime

from openscan.controllers.hardware.cameras.camera import get_all_camera_controllers, get_camera_controller
from openscan.controllers.services import projects, cloud
from openscan.controllers.services.projects import ProjectManager
import openscan.controllers.services.scans as scans #import start_scan, cancel_scan, pause_scan, resume_scan
from openscan.models.project import Project
from openscan.config.scan import ScanSetting
from openscan.models.scan import Scan, ScanStatus
from openscan.models.task import Task, TaskStatus

from openscan.controllers.services.projects import get_project_manager
from openscan.controllers.services.tasks.task_manager import task_manager, get_task_manager

router = APIRouter(
    prefix="/projects",
    tags=["projects"],
    responses={404: {"description": "Not found"}},
)


# Response models
class ScanStatusResponse(BaseModel):
    status: str
    current_step: int
    total_steps: int
    duration: float
    last_updated: Optional[str]
    system_message: Optional[str]


class ScanControlResponse(BaseModel):
    success: bool
    message: str
    scan: Scan


#project_manager = get_project_manager()

@router.get("/", response_model=dict[str, Project])
async def get_projects():
    """Get all projects with serialized data"""
    project_manager = get_project_manager()
    projects_dict = project_manager.get_all_projects()
    # Convert to serializable format
    return {name: jsonable_encoder(project) for name, project in projects_dict.items()}


@router.get("/{project_name}", response_model=Project)
async def get_project(project_name: str):
    """Get a project"""
    project_manager = get_project_manager()
    project = project_manager.get_project_by_name(project_name)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project {project_name} not found")
    return project


@router.post("/{project_name}", response_model=Project)
async def new_project(project_name: str, project_description: Optional[str] = ""):
    """Create a new project"""
    try:
        project_manager = get_project_manager()
        return project_manager.add_project(project_name, project_description)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Project {project_name} already exists.")


@router.post("/{project_name}/scan", response_model=Scan)
async def add_scan_with_description(project_name: str,
                   camera_name: str,
                   scan_settings: ScanSetting,
                   scan_description:  Optional[str] = ""):
    """Add a new scan to a project"""
    camera_controller = get_camera_controller(camera_name)
    project_manager = get_project_manager()

    try:
        scan = project_manager.add_scan(project_name, camera_controller, scan_settings, scan_description)

        # Pass the initialized project_manager and the scan object to the task.
        #await task_manager.create_and_run_task("scan_task", scan, controller, project_manager)
        task = await scans.start_scan(project_manager, scan, camera_controller)
        success = task is not None

        return scan

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start scan: {e}")



# Cloud uploads --------------------------------------------------------------


@router.post("/{project_name}/upload", response_model=Task)
async def upload_project_to_cloud(project_name: str, token_override: Optional[str] = None) -> Task:
    """Schedule an asynchronous cloud upload for a project."""

    try:
        task = await cloud.upload_project(project_name, token=token_override)
    except cloud.CloudServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return task


@router.get("/{project_name}/scans/{scan_index}", response_model=Scan)
async def get_scan(project_name: str, scan_index: int):
    """Get Scan by project and index"""
    try:
        project_manager = get_project_manager()
        return project_manager.get_scan_by_index(project_name, scan_index)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{project_name}/{scan_index}/", response_model=bool)
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
        return project_manager.delete_photos(scan, photo_filenames)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Project {project_name} not found")


@router.delete("/{project_name}", response_model=bool)
async def delete_project(project_name: str):
    """Delete a project"""
    project_manager = get_project_manager()
    project = project_manager.get_project_by_name(project_name)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project {project_name} not found")

    return project_manager.delete_project(project)

@router.delete("/{project_name}/scans/", response_model=bool)
async def delete_scan(project_name: str, scan_index: int):
    """Delete a scan from a project"""
    project_manager = get_project_manager()
    scan = project_manager.get_scan_by_index(project_name, scan_index)
    try:
        return project_manager.delete_scan(scan)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Project {project_name} not found")


@router.get("/{project_name}/scans/{scan_index}/status", response_model=ScanStatusResponse)
async def get_scan_status(project_name: str, scan_index: int):
    """Get the current status of a scan"""
    try:
        project_manager = get_project_manager()
        scan = project_manager.get_scan_by_index(project_name, scan_index)
        if not scan:
            raise HTTPException(status_code=404, detail=f"Scan {scan_index} not found")

        return ScanStatusResponse(
            status=scan.status.value,
            current_step=scan.current_step,
            total_steps=scan.settings.points,
            duration=scan.duration,
            last_updated=scan.last_updated.isoformat() if scan.last_updated else None,
            system_message=scan.system_message
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/{project_name}/scans/{scan_index}/pause", response_model=ScanControlResponse)
async def pause_scan(project_name: str, scan_index: int):
    """Pause a running scan"""
    project_manager = get_project_manager()
    scan = project_manager.get_scan_by_index(project_name, scan_index)
    if not scan:
        raise HTTPException(status_code=404, detail=f"Scan {scan_index} not found")

    updated_task = await scans.pause_scan(scan)
    success = updated_task is not None

    return ScanControlResponse(
        success=success,
        message="Scan paused successfully." if success else "Failed to pause scan. It might not be running.",
        scan=scan
    )


@router.patch("/{project_name}/scans/{scan_index}/resume", response_model=ScanControlResponse)
async def resume_scan(project_name: str, scan_index: int, camera_name: str):
    """Resume a paused, cancelled or failed scan"""
    try:

        camera_controller = get_camera_controller(camera_name)
        project_manager = get_project_manager()
        scan = project_manager.get_scan_by_index(project_name, scan_index)
        if not scan:
            raise HTTPException(status_code=404, detail=f"Scan {scan_index} not found")

        #task_manager = get_task_manager()
        task = task_manager.get_task_info(scan.task_id) if scan.task_id else None

        if task and task.status == TaskStatus.PAUSED:
            updated_task = await scans.resume_scan(scan)
            message = "Scan resumed successfully." if updated_task else "Failed to resume paused scan."
        elif not task or task.status in [TaskStatus.COMPLETED, TaskStatus.CANCELLED, TaskStatus.ERROR]:
            # This logic handles resuming an INTERRUPTED scan or restarting a failed/completed one.
            updated_task = await scans.start_scan(
                project_manager,
                scan,
                camera_controller,
                start_from_step=scan.current_step
            )
            message = "Scan restarted successfully." if updated_task else "Failed to restart scan."
        else:
            raise HTTPException(status_code=400, detail=f"Scan cannot be resumed from its current state: {task.status.value}")

        resumed_task = await scans.resume_scan(scan)
        success = resumed_task is not None
        return ScanControlResponse(
            success=success,
            message="Scan resumed" if success else "Failed to resume scan",
            scan=scan
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/{project_name}/scans/{scan_index}/cancel", response_model=ScanControlResponse)
async def cancel_scan(project_name: str, scan_index: int):
    """Cancel a running scan"""
    try:
        project_manager = get_project_manager()
        scan = project_manager.get_scan_by_index(project_name, scan_index)
        if not scan:
            raise HTTPException(status_code=404, detail=f"Scan {scan_index} not found")

        updated_task = await scans.cancel_scan(scan)
        success = updated_task is not None

        return ScanControlResponse(
            success=success,
            message="Scan cancelled successfully" if success else "Failed to cancel scan",
            scan=scan
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
        response = StreamingResponse(
            zs,
            media_type="application/zip",
            headers={
                "Content-Disposition": f"attachment; filename={project_name}.zip",
                "Content-Length": str(len(zs)),
                "Last-Modified": str(zs.last_modified),
            }
        )

        return response
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Project {project_name} not found")
    except Exception as e:
        print(e)
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
                        print(f"Scan with index {scan_index} not found")
                        continue
                    scan_dir = os.path.join(project.path, f"scan{scan_index:02d}")
                    if os.path.exists(scan_dir):
                        zs.add_path(scan_dir, f"scan{scan_index:02d}")
                except Exception as e:
                    print(e)
                    continue
        else:
            filename = f"{project_name}_all_scans.zip"
            for scan_id, scan in project.scans.items():
                scan_dir = os.path.join(project.path, f"scan_{scan.index}")
                if os.path.exists(scan_dir):
                    zs.add_path(scan_dir, f"scan_{scan.index}")

        zs.add(_serialize_project_for_zip(project), "project_metadata.json")

        response = StreamingResponse(
            zs,
            media_type="application/zip",
            headers={
                "Content-Disposition": f"attachment; filename={filename}",
                "Content-Length": str(len(zs)),
                "Last-Modified": str(zs.last_modified),
            }
        )
        return response
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Project {project_name} not found")
    except Exception as e:
        print(e)
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{project_name}/scans/{scan_index}", response_model=Scan)
async def get_scan(project_name: str, scan_index: int):
    """Get Scan by project and index"""
    try:
        project_manager = get_project_manager()
        return project_manager.get_scan_by_index(project_name, scan_index)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
