from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from openscan_firmware.config.external_trigger_run import ExternalTriggerRunSettings
from openscan_firmware.controllers.services.external_trigger_runs import (
    cancel_external_trigger_run,
    get_external_trigger_task,
    get_external_trigger_run_manager,
    list_external_trigger_tasks,
    pause_external_trigger_run,
    resume_external_trigger_run,
    start_external_trigger_run,
)
from openscan_firmware.models.external_trigger_run import ExternalTriggerRunPath
from openscan_firmware.models.task import Task


router = APIRouter(
    prefix="/external-trigger/runs",
    tags=["external-trigger"],
    responses={404: {"description": "Not found"}},
)


class ExternalTriggerRunCreateRequest(BaseModel):
    label: str | None = None
    description: str | None = None
    settings: ExternalTriggerRunSettings


def _get_existing_task_or_404(task_id: str) -> Task:
    task = get_external_trigger_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"External trigger run '{task_id}' not found.")
    return task


@router.get("/", response_model=list[Task])
async def list_external_trigger_runs() -> list[Task]:
    return list_external_trigger_tasks()


@router.post("/", response_model=Task, status_code=status.HTTP_202_ACCEPTED)
async def create_external_trigger_run(request: ExternalTriggerRunCreateRequest) -> Task:
    try:
        task = await start_external_trigger_run(
            label=request.label,
            description=request.description,
            settings=request.settings,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return task


@router.get("/{task_id}", response_model=Task)
async def get_external_trigger_run(task_id: str) -> Task:
    return _get_existing_task_or_404(task_id)


@router.get("/{task_id}/path", response_model=ExternalTriggerRunPath)
async def get_external_trigger_run_path(task_id: str) -> ExternalTriggerRunPath:
    path_data = get_external_trigger_run_manager().get_path_data(task_id)
    if path_data is not None:
        return path_data

    if get_external_trigger_task(task_id) is None:
        raise HTTPException(status_code=404, detail=f"External trigger run '{task_id}' not found.")
    raise HTTPException(status_code=404, detail=f"Path for external trigger run '{task_id}' not available.")


@router.patch("/{task_id}/cancel", response_model=Task)
async def cancel_external_trigger_run_endpoint(task_id: str) -> Task:
    task = await cancel_external_trigger_run(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"External trigger run '{task_id}' not found.")
    return task


@router.patch("/{task_id}/pause", response_model=Task)
async def pause_external_trigger_run_endpoint(task_id: str) -> Task:
    task = await pause_external_trigger_run(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"External trigger run '{task_id}' not found.")
    return task


@router.patch("/{task_id}/resume", response_model=Task)
async def resume_external_trigger_run_endpoint(task_id: str) -> Task:
    task = await resume_external_trigger_run(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"External trigger run '{task_id}' not found.")
    return task
