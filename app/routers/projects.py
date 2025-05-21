from fastapi import APIRouter, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from fastapi_versionizer import api_version
from pydantic import BaseModel
import pathlib
from typing import Optional, List
import asyncio
import os
import json
from datetime import datetime

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


@api_version(0,1)
@router.get("/", response_model=dict[str, Project])
async def get_projects():
    """Get all projects with serialized data"""
    projects_dict = project_manager.get_all_projects()
    # Convert to serializable format
    return {name: jsonable_encoder(project) for name, project in projects_dict.items()}


@api_version(0,1)
@router.get("/{project_name}", response_model=Project)
async def get_project(project_name: str):
    """Get a project"""
    try:
        return jsonable_encoder(project_manager.get_project_by_name(project_name))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Project {project_name} not found")


@api_version(0,1)
@router.delete("/{project_name}", response_model=bool)
async def delete_project(project_name: str):
    """Delete a project"""
    try:
        return project_manager.delete_project(projects.get_project(project_name))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Project {project_name} not found")


@api_version(0,2)
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
    try:
        scan = project_manager.get_scan_by_index(project_name, scan_index)
        return project_manager.delete_photos(scan, photo_filenames)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Project {project_name} not found")

@api_version(0,1)
@router.post("/{project_name}", response_model=Project)
async def new_project(project_name: str):
    """Create a new project"""
    try:
        return project_manager.add_project(project_name)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Project {project_name} already exists.")

@api_version(0,2)
@router.post("/{project_name}", response_model=Project)
async def new_project(project_name: str, project_description: Optional[str] = ""):
    """Create a new project"""
    try:
        return project_manager.add_project(project_name, project_description)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Project {project_name} already exists.")

@api_version(0,1)
@router.post("/{project_name}/scan", response_model=Scan)
async def add_scan(project_name: str, camera_name: str, scan_settings: ScanSetting):
    """Add a new scan to a project"""
    controller = get_camera_controller(camera_name)
    scan = project_manager.add_scan(project_name, controller, scan_settings)

    scan_manager = get_scan_manager(scan, project_manager)

    asyncio.create_task(scan_manager.start_scan(controller))

    return scan

@api_version(0,2)
@router.post("/{project_name}/scan", response_model=Scan)
async def add_scan_with_description(project_name: str,
                   camera_name: str,
                   scan_settings: ScanSetting,
                   scan_description:  Optional[str] = ""):
    """Add a new scan to a project"""
    controller = get_camera_controller(camera_name)
    scan = project_manager.add_scan(project_name, controller, scan_settings, scan_description)

    scan_manager = get_scan_manager(scan, project_manager)

    asyncio.create_task(scan_manager.start_scan(controller))

    return scan


@api_version(0,1)
@router.get("/{project_name}/scans/{scan_index}", response_model=Scan)
async def get_scan(project_name: str, scan_index: int):
    """Get Scan by project and index"""
    try:
        return project_manager.get_scan_by_index(project_name, scan_index)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@api_version(0,2)
@router.delete("/{project_name}", response_model=bool)
async def delete_scan(project_name: str, scan_index: int):
    """Delete a scan from a project"""
    scan = project_manager.get_scan_by_index(project_name, scan_index)
    try:
        return project_manager.delete_scan(scan)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Project {project_name} not found")


@api_version(0,1)
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


@api_version(0,1)
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


@api_version(0,1)
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


@api_version(0,1)
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


@api_version(0,1)
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


@api_version(0,1)
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
        # Import zipstream-ng
        from zipstream import ZipStream

        # Get project
        project = project_manager.get_project_by_name(project_name)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project {project_name} not found")

        # Create ZipStream
        zs = ZipStream(sized=True)

        # Set the zip file's comment
        zs.comment = f"OpenScan3 Project: {project_name} - Generated on {datetime.now().isoformat()}"

        # If scan_indices is provided, only include those scans
        if scan_indices:
            for scan_index in scan_indices:
                try:
                    scan = project_manager.get_scan_by_index(project_name, scan_index)
                    if not scan:
                        print(f"Scan with index {scan_index} not found")
                        continue

                    # Add scan directory to zip
                    scan_dir = os.path.join(project.path, f"scan{scan_index:02d}")
                    if os.path.exists(scan_dir):
                        # Use the scan index as the top-level folder name
                        zs.add_path(scan_dir, f"scan{scan_index:02d}")
                except Exception as e:
                    print(e)
                    # Skip scans that don't exist or can't be added
                    continue
        else:
            # Include all scans
            for scan_id, scan in project.scans.items():
                scan_dir = os.path.join(project.path, f"scan_{scan.index}")
                if os.path.exists(scan_dir):
                    zs.add_path(scan_dir, f"scan_{scan.index}")

        # Add project metadata
        zs.add(_serialize_project_for_zip(project), "project_metadata.json")

        # Return streaming response
        response = StreamingResponse(
            zs,
            media_type="application/zip",
            headers={
                "Content-Disposition": f"attachment; filename={project_name}_scans.zip",
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

