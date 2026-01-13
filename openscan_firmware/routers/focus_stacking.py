"""API endpoints for managing focus stacking tasks."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from openscan_firmware.controllers.services import focus_stacking as focus_service
from openscan_firmware.models.task import Task

router = APIRouter(prefix="/projects", tags=["focus_stacking"])


@router.post("/{project_name}/scans/{scan_index:int}/focus-stacking/start", response_model=Task)
async def start_focus_stacking(project_name: str, scan_index: int) -> Task:
    """Start focus stacking for a scan."""
    try:
        return await focus_service.start_focus_stacking(project_name, scan_index)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - unexpected errors bubble up as 500
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.patch("/{project_name}/scans/{scan_index:int}/focus-stacking/pause", response_model=Task)
async def pause_focus_stacking(project_name: str, scan_index: int) -> Task:
    """Pause an active focus stacking task."""
    try:
        task = await focus_service.pause_focus_stacking(project_name, scan_index)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - unexpected errors bubble up as 500
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if task is None:
        raise HTTPException(status_code=409, detail="Focus stacking is not running")

    return task


@router.patch("/{project_name}/scans/{scan_index:int}/focus-stacking/resume", response_model=Task)
async def resume_focus_stacking(project_name: str, scan_index: int) -> Task:
    """Resume a paused focus stacking task."""
    try:
        task = await focus_service.resume_focus_stacking(project_name, scan_index)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - unexpected errors bubble up as 500
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if task is None:
        raise HTTPException(status_code=409, detail="Focus stacking is not paused")

    return task


@router.patch("/{project_name}/scans/{scan_index:int}/focus-stacking/cancel", response_model=Task)
async def cancel_focus_stacking(project_name: str, scan_index: int) -> Task:
    """Cancel an active focus stacking task."""
    try:
        task = await focus_service.cancel_focus_stacking(project_name, scan_index)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - unexpected errors bubble up as 500
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if task is None:
        raise HTTPException(status_code=409, detail="Focus stacking is not running")

    return task
