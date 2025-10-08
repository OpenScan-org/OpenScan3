from typing import List, Any, Dict

from fastapi import APIRouter, HTTPException, status, Body
from fastapi_versionizer import api_version

from openscan.controllers.services.tasks.task_manager import get_task_manager
from openscan.models.task import Task, TaskStatus


router = APIRouter(
    prefix="/tasks",
    tags=["tasks"],
    responses={404: {"description": "Not found"}},
)


@api_version(0,3)
@router.get("/", response_model=List[Task])
async def get_all_tasks():
    """
    Retrieve a list of all tasks known to the task manager.
    """
    task_manager = get_task_manager()
    return task_manager.get_all_tasks_info()


@api_version(0,3)
@router.get("/{task_id}", response_model=Task)
async def get_task_status(task_id: str):
    """
    Retrieve the status and details of a specific task.
    """
    task_manager = get_task_manager()
    task = task_manager.get_task_info(task_id)
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return task


@api_version(0,3)
@router.delete("/{task_id}", response_model=Task)
async def cancel_task(task_id: str):
    """
    Request cancellation of a running task.
    """
    task_manager = get_task_manager()
    task = await task_manager.cancel_task(task_id)
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return task


@api_version(0,3)
@router.post("/{task_id}/pause", response_model=Task, summary="Pause a Task")
async def pause_task(task_id: str):
    """
    Pauses a running task.

    - **task_id**: The ID of the task to pause.
    """
    task_manager = get_task_manager()
    task = await task_manager.pause_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found or cannot be paused.")
    if task.status not in [TaskStatus.PAUSED, TaskStatus.RUNNING]:
        pass
    return task


@api_version(0,3)
@router.post("/{task_id}/resume", response_model=Task, summary="Resume a Task")
async def resume_task(task_id: str):
    """
    Resumes a paused task.

    - **task_id**: The ID of the task to resume.
    """
    task_manager = get_task_manager()
    task = await task_manager.resume_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found or cannot be resumed.")
    if task.status not in [TaskStatus.RUNNING, TaskStatus.PAUSED]:
        pass
    return task


@api_version(0,3)
@router.post("/{task_name}", response_model=Task, status_code=status.HTTP_202_ACCEPTED)
async def create_task(
    task_name: str,
    args: List[Any] = Body(default=[], description="Positional arguments for the task"),
    kwargs: Dict[str, Any] = Body(default={}, description="Keyword arguments for the task")
):
    """
    Create and start a new background task with optional parameters.

    The request body accepts:
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
    try:
        task_manager = get_task_manager()
        task = await task_manager.create_and_run_task(task_name, *args, **kwargs)
        return task
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))