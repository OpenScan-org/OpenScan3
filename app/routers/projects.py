from fastapi import APIRouter, HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel
import pathlib
from typing import Optional
import asyncio

from app.controllers.hardware.cameras.camera import get_all_camera_controllers, get_camera_controller
from controllers.services import projects
from controllers.services.projects import ProjectManager
from controllers.services.scans import get_scan_manager, get_active_scan_manager
from app.models.project import Project
from app.config.scan import ScanSetting
from app.models.scan import Scan, ScanStatus

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

project_manager = projects.ProjectManager(str(pathlib.PurePath("projects")))

@router.get("/", response_model=dict[str, Project])
async def get_projects():
    """Get all projects with serialized data"""
    projects_dict = project_manager.get_all_projects()
    # Convert to serializable format
    return {name: jsonable_encoder(project) for name, project in projects_dict.items()}


@router.get("/{project_name}", response_model=Project)
async def get_project(project_name: str):
    """Get a project"""
    try:
        return jsonable_encoder(project_manager.get_project_by_name(project_name))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Project {project_name} not found")

@router.delete("/{project_name}", response_model=bool)
async def delete_project(project_name: str):
    """Delete a project"""
    try:
        return project_manager.delete_project(projects.get_project(project_name))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Project {project_name} not found")


@router.post("/{project_name}", response_model=Project)
async def new_project(project_name: str):
    """Create a new project"""
    try:
        return project_manager.add_project(project_name)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Project {project_name} already exists.")


@router.post("/{project_name}/scan", response_model=Scan)
async def add_scan(project_name: str, camera_name: str, scan_settings: ScanSetting):
    """Add a new scan to a project"""
    controller = get_camera_controller(camera_name)
    scan = project_manager.add_scan(project_name, controller, scan_settings)

    scan_manager = get_scan_manager(scan, project_manager)

    asyncio.create_task(scan_manager.start_scan(controller))

    return scan

@router.get("/{project_name}/scans/{scan_index}", response_model=Scan)
async def get_scan(project_name: str, scan_index: int):
    """Get Scan by project and index"""
    try:
        return project_manager.get_scan_by_index(project_name, scan_index)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{project_name}/scans/{scan_index}/status", response_model=ScanStatusResponse)
async def get_scan_status(project_name: str, scan_index: int):
    """Get the current status of a scan"""
    try:
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
    try:
        scan = project_manager.get_scan_by_index(project_name, scan_index)
        if not scan:
            raise HTTPException(status_code=404, detail=f"Scan {scan_index} not found")

        scan_manager = get_active_scan_manager()
        if scan_manager is None or scan_manager._scan != scan:
            raise HTTPException(status_code=409, detail="No active scan found")

        success = await scan_manager.pause()
        return ScanControlResponse(
            success=success,
            message="Scan paused" if success else "Failed to pause scan",
            scan=scan
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/{project_name}/scans/{scan_index}/resume", response_model=ScanControlResponse)
async def resume_scan(project_name: str, scan_index: int, camera_name: str):
    """Resume a paused, cancelled or failed scan"""
    camera_controller = get_camera_controller(camera_name)
    try:
        scan = project_manager.get_scan_by_index(project_name, scan_index)
        if not scan:
            raise HTTPException(status_code=404, detail=f"Scan {scan_index} not found")

        # If no active manager exists, create a new one for this scan
        scan_manager = get_active_scan_manager()
        if scan_manager is None:
            try:
                scan_manager = get_scan_manager(scan, project_manager)
            except RuntimeError as e:
                raise HTTPException(status_code=409, detail=str(e))

        # Check if the right scan is active
        if scan_manager._scan != scan:
            raise HTTPException(status_code=409, detail="This is not the active scan")

        # Allow resume for paused, cancelled and failed scans
        if scan.status not in [ScanStatus.PAUSED, ScanStatus.CANCELLED, ScanStatus.ERROR]:
            raise HTTPException(
                status_code=409,
                detail=f"Scan cannot be resumed (current status: {scan.status.value})"
            )

        success = await scan_manager.resume(camera_controller)
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
        scan = project_manager.get_scan_by_index(project_name, scan_index)
        if not scan:
            raise HTTPException(status_code=404, detail=f"Scan {scan_index} not found")

        scan_manager = get_active_scan_manager()
        if scan_manager is None or scan_manager._scan != scan:
            raise HTTPException(status_code=409, detail="No active scan found")

        success = await scan_manager.cancel()
        return ScanControlResponse(
            success=success,
            message="Scan cancelled successfully" if success else "Failed to cancel scan",
            scan=scan
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
