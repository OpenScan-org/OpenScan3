from typing import List, Any, Dict

from fastapi import APIRouter, HTTPException, Response, status, Body

from openscan_firmware.controllers.services.tasks.task_manager import get_task_manager
from openscan_firmware.models.task import Task, TaskStatus


router = APIRouter(
    prefix="/tasks",
    tags=["tasks"],
    responses={404: {"description": "Not found"}},
)


_DOMAIN_TASK_ENDPOINTS = {
    "scan_task": "POST /projects/{project_name}/scan",
    "focus_stacking_task": (
        "POST /projects/{project_name}/scans/{scan_index}/focus-stacking/start"
    ),
    "cloud_upload_task": "POST /projects/{project_name}/upload",
}


@router.get("/", response_model=List[Task])
async def get_all_tasks():
    """
    Retrieve a list of all tasks known to the task manager.

    Returns:
        List[Task]: A list of all tasks known to the task manager.
    """
    task_manager = get_task_manager()
    return task_manager.get_all_tasks_info()


@router.get("/{task_id}", response_model=Task)
async def get_task_status(task_id: str):
    """
    Retrieve the status and details of a specific task.

    Args:
        task_id: The ID of the task to retrieve.

    Returns:
        Task: The task object with its status and details.
    """
    task_manager = get_task_manager()
    task = task_manager.get_task_info(task_id)
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return task


@router.delete("/{task_id}", response_model=Task)
async def cancel_task(task_id: str):
    """
    Request cancellation of a running task.

    Args:
        task_id: The ID of the task to cancel.

    Returns:
        Task: The task object with its status and details.
    """
    task_manager = get_task_manager()
    task = await task_manager.cancel_task(task_id)
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return task


@router.delete(
    "/{task_id}/cleanup",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a terminal task record",
)
async def delete_task(task_id: str) -> Response:
    """Remove a terminal task from persistence and memory."""
    task_manager = get_task_manager()
    try:
        await task_manager.delete_task(task_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{task_id}/pause", response_model=Task, summary="Pause a Task")
async def pause_task(task_id: str):
    """
    Pauses a running task.

    Args:
        task_id: The ID of the task to pause.

    Returns:
        Task: The task object with its status and details.
    """
    task_manager = get_task_manager()
    task = await task_manager.pause_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found or cannot be paused.")
    if task.status not in [TaskStatus.PAUSED, TaskStatus.RUNNING]:
        pass
    return task


@router.post("/{task_id}/resume", response_model=Task, summary="Resume a Task")
async def resume_task(task_id: str):
    """
    Resumes a paused or interrupted task.

    Args:
        task_id: The ID of the task to resume.

    Returns:
        Task: The task object with its status and details.
    """
    task_manager = get_task_manager()
    task = await task_manager.resume_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found or cannot be resumed.")
    if task.status not in [TaskStatus.RUNNING, TaskStatus.PAUSED]:
        pass
    return task


@router.post("/{task_name}", response_model=Task, status_code=status.HTTP_202_ACCEPTED)
async def create_task(
    task_name: str,
    args: List[Any] = Body(default=[], description="Positional arguments for the task"),
    kwargs: Dict[str, Any] = Body(default={}, description="Keyword arguments for the task"),
    depends_on: str | None = Body(
        default=None,
        description="Optional task ID that must complete successfully before this task runs",
    ),
):
    """
    Create and start an experimental or custom background task.

    Domain-owned tasks such as scans, focus stacking, and cloud uploads must
    be started through their project-specific endpoints. Those endpoints also
    persist the task reference and maintain the corresponding domain status.

    The request body accepts:
    - **depends_on**: Optional ID of a prerequisite task
    - **args**: List of positional arguments (e.g., `["project_name", 0]`)
    - **kwargs**: Dictionary of keyword arguments (e.g., `{"num_batches": 5}`)

    Args:
        task_name: The name of the task to create, as registered in the TaskManager.
        args: Positional arguments to pass to the task's run method.
        kwargs: Keyword arguments to pass to the task's run method.

    Returns:
        The created task object.

    Examples:
        ```json
        // No parameters
        {}

        // With positional args
        {
            "args": ["MyProject", 0]
        }

        // With keyword args
        {
            "kwargs": {"num_calibration_batches": 5}
        }

        // With both
        {
            "args": ["MyProject", 0],
            "kwargs": {"num_calibration_batches": 5}
        }
        ```
    """
    domain_endpoint = _DOMAIN_TASK_ENDPOINTS.get(task_name)
    if domain_endpoint:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Task '{task_name}' is managed by a project-specific endpoint. "
                f"Use {domain_endpoint} instead. The generic /tasks/{{task_name}} "
                "endpoint is intended for experimental or custom tasks."
            ),
        )

    try:
        task_manager = get_task_manager()
        task = await task_manager.create_and_run_task(
            task_name,
            *args,
            depends_on=depends_on,
            **kwargs,
        )
        return task
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
