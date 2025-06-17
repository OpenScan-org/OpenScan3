from fastapi import APIRouter, HTTPException, Query, Depends
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from fastapi_versionizer import api_version
from pydantic import BaseModel
from typing import Optional, List
import json
from datetime import datetime

from app.controllers.hardware.cameras.camera import get_camera_controller, CameraController
from app.controllers.services import scans
from app.controllers.services.projects import ProjectManager, get_project_manager
from app.controllers.services.tasks.task_manager import get_task_manager, TaskManager
from app.models.project import Project
from app.config.scan import ScanSetting
from app.models.scan import Scan
from app.models.task import Task, TaskStatus

router = APIRouter(
    prefix="/projects",
    tags=["projects"],
    responses={404: {"description": "Not found"}},
)


# --- Response Models for backward compatibility ---

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

# --- Project Endpoints ---

@api_version(0, 1)
@router.get("/", response_model=dict[str, Project])
async def get_projects(project_manager: ProjectManager = Depends(get_project_manager)):
    """Get all projects with serialized data"""
    projects_dict = project_manager.get_all_projects()
    return jsonable_encoder(projects_dict)


@api_version(0, 1)
@router.get("/{project_name}", response_model=Project)
async def get_project(project_name: str, project_manager: ProjectManager = Depends(get_project_manager)):
    """Get a project by its name"""
    project = project_manager.get_project_by_name(project_name)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project '{project_name}' not found")
    return project


@api_version(0, 1)
@router.delete("/{project_name}", status_code=204)
async def delete_project(project_name: str, project_manager: ProjectManager = Depends(get_project_manager)):
    """Delete a project and all its data"""
    project = project_manager.get_project_by_name(project_name)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project '{project_name}' not found")
    project_manager.delete_project(project)
    return None


@api_version(0, 1)
@router.post("/", response_model=Project, status_code=201)
async def new_project(
    project_name: str,
    project_description: Optional[str] = "",
    project_manager: ProjectManager = Depends(get_project_manager),
):
    """Create a new project"""
    try:
        return project_manager.add_project(project_name, project_description)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


# --- Scan Endpoints ---

@api_version(0, 1)
@router.post("/{project_name}/scans", response_model=Scan, status_code=202)
async def create_and_start_scan(
    project_name: str,
    camera_name: str,
    scan_settings: ScanSetting,
    scan_description: Optional[str] = "",
    project_manager: ProjectManager = Depends(get_project_manager),
    camera_controller: CameraController = Depends(get_camera_controller),
):
    """Adds a new scan to a project and starts the corresponding task."""
    project = project_manager.get_project_by_name(project_name)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project '{project_name}' not found")

    scan = await project_manager.add_scan(project_name, camera_controller, scan_settings, scan_description)

    task = await scans.start_scan(project_manager, scan, camera_controller)
    if not task:
        # Although start_scan should handle this, we add a fallback.
        raise HTTPException(status_code=400, detail="Failed to start scan task. It might be already running or another exclusive task is active.")

    # The scan object now has the task_id assigned by start_scan
    return scan


@api_version(0, 1)
@router.get("/{project_name}/scans/{scan_index}", response_model=Scan)
async def get_scan(
    project_name: str, scan_index: int, project_manager: ProjectManager = Depends(get_project_manager)
):
    """Get a specific scan from a project"""
    scan = await project_manager.get_scan_by_index(project_name, scan_index)
    if not scan:
        raise HTTPException(status_code=404, detail=f"Scan {scan_index} in project '{project_name}' not found")
    return scan


@api_version(0, 1)
@router.get("/{project_name}/scans/{scan_index}/status", response_model=ScanStatusResponse)
async def get_scan_status(
    project_name: str,
    scan_index: int,
    project_manager: ProjectManager = Depends(get_project_manager),
    task_manager: TaskManager = Depends(get_task_manager),
):
    """Get the status of a scan, mapped to the legacy response model."""
    scan = await project_manager.get_scan_by_index(project_name, scan_index)
    if not scan:
        raise HTTPException(status_code=404, detail=f"Scan {scan_index} not found")

    task = task_manager.get_task(scan.task_id) if scan.task_id else None

    if task:
        return ScanStatusResponse(
            status=task.status.value,
            current_step=task.progress.current if task.progress else scan.current_step,
            total_steps=task.progress.total if task.progress else scan.settings.points,
            duration=task.elapsed_time,
            last_updated=datetime.now().isoformat(),
            system_message=task.error or (task.progress.message if task.progress else None)
        )

    # Handle cases where there is no active task
    # If a task_id exists but the task is not in the manager, it's interrupted.
    if scan.task_id:
        status = "interrupted"
        message = "Scan was interrupted (e.g., application restart)."
    # If there's no task_id, it's either completed, cancelled, or pending.
    # Without a status on the scan model, we can only make an educated guess.
    # We assume 'completed' if it has photos and 'pending' otherwise.
    else:
        if scan.photos:
            status = "completed"
            message = "Scan appears to be completed."
        else:
            status = "pending"
            message = "Scan is pending, has not been started yet."

    return ScanStatusResponse(
        status=status,
        current_step=scan.current_step,
        total_steps=scan.settings.points,
        duration=0, # No active task, so duration is not tracked
        last_updated=scan.last_updated.isoformat(),
        system_message=scan.system_message or message
    )


@api_version(0, 1)
@router.patch("/{project_name}/scans/{scan_index}/pause", response_model=ScanControlResponse)
async def pause_scan(
    project_name: str, scan_index: int, project_manager: ProjectManager = Depends(get_project_manager)
):
    """Pauses a running scan."""
    scan = await project_manager.get_scan_by_index(project_name, scan_index)
    if not scan:
        raise HTTPException(status_code=404, detail=f"Scan {scan_index} not found")

    updated_task = await scans.pause_scan(scan)
    success = updated_task is not None

    return ScanControlResponse(
        success=success,
        message="Scan paused successfully." if success else "Failed to pause scan. It might not be running.",
        scan=scan
    )


@api_version(0, 1)
@router.patch("/{project_name}/scans/{scan_index}/resume", response_model=ScanControlResponse)
async def resume_scan(
    project_name: str,
    scan_index: int,
    project_manager: ProjectManager = Depends(get_project_manager),
    camera_controller: CameraController = Depends(get_camera_controller) # Needed for restart
):
    """Resumes a paused or interrupted scan."""
    scan = await project_manager.get_scan_by_index(project_name, scan_index)
    if not scan:
        raise HTTPException(status_code=404, detail=f"Scan {scan_index} not found")

    task_manager = get_task_manager()
    task = task_manager.get_task(scan.task_id) if scan.task_id else None

    updated_task: Optional[Task] = None
    message = ""

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

    return ScanControlResponse(
        success=updated_task is not None,
        message=message,
        scan=scan
    )


@api_version(0, 1)
@router.patch("/{project_name}/scans/{scan_index}/cancel", response_model=ScanControlResponse)
async def cancel_scan(
    project_name: str, scan_index: int, project_manager: ProjectManager = Depends(get_project_manager)
):
    """Cancels a running or paused scan."""
    scan = await project_manager.get_scan_by_index(project_name, scan_index)
    if not scan:
        raise HTTPException(status_code=404, detail=f"Scan {scan_index} not found")

    updated_task = await scans.cancel_scan(scan)
    success = updated_task is not None

    return ScanControlResponse(
        success=success,
        message="Scan cancelled successfully." if success else "Failed to cancel scan. No active task found.",
        scan=scan
    )


# --- Photo and File Endpoints ---


@api_version(1, 0)
@router.delete("/{project_name}/scans/{scan_index}/photos", status_code=204)
async def delete_photos(
    project_name: str,
    scan_index: int,
    photo_filenames: list[str],
    project_manager: ProjectManager = Depends(get_project_manager),
):
    """Delete specific photos from a scan."""
    scan = await project_manager.get_scan_by_index(project_name, scan_index)
    if not scan:
        raise HTTPException(status_code=404, detail=f"Scan {scan_index} not found")

    await project_manager.delete_photos(scan, photo_filenames)
    return None


@api_version(0, 1)
@router.get("/{project_name}/zip")
async def download_project_zip(
    project_name: str, project_manager: ProjectManager = Depends(get_project_manager)
):
    """Download a full project as a ZIP archive."""
    project = project_manager.get_project_by_name(project_name)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project '{project_name}' not found")

    from zipstream import ZipStream

    def generator():
        zs = ZipStream.from_path(project.path)
        # Add project metadata as a separate file
        project_dict = jsonable_encoder(project)
        project_json = json.dumps(project_dict, indent=2)
        zs.add(project_json.encode(), "project_metadata.json")
        yield from zs

    response = StreamingResponse(generator(), media_type="application/zip")
    response.headers["Content-Disposition"] = f"attachment; filename={project_name}.zip"
    return response
