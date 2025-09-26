from typing import List

from fastapi import APIRouter, HTTPException, status
from fastapi_versionizer import api_version

from app.controllers.services.tasks.task_manager import get_task_manager
from app.models.task import Task, TaskStatus


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
    if task.status not in [TaskStatus.PAUSED, TaskStatus.RUNNING]: # RUNNING if it couldn't be paused immediately
        # This case might indicate the task wasn't running or an issue occurred.
        # The TaskManager logs more details.
        pass # Return current task state
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
    if task.status not in [TaskStatus.RUNNING, TaskStatus.PAUSED]: # PAUSED if it couldn't be resumed immediately
        # This case might indicate the task wasn't paused or an issue occurred.
        pass # Return current task state
    return task


@api_version(0,3)
@router.post("/crop", response_model=Task)
async def crop(camera_name: str):
    """Example image cropping task"""

    # The task will get the controller itself using the camera_name.
    # We only pass the serializable name string here to avoid serialization errors.
    task_manager = get_task_manager()

    task = await task_manager.create_and_run_task("crop_task", camera_name)
    return task


@api_version(0,3)
@router.post("/hello-world-async", response_model=Task)
async def hello_world_async(total_steps: int, delay: float):
    """Start the async hello world demo task."""

    task_manager = get_task_manager()

    # Updated to explicit task_name with required _task suffix
    task = await task_manager.create_and_run_task("hello_world_async_task", total_steps, delay)
    return task

@api_version(0,3)
@router.post("/{task_name}", response_model=Task, status_code=status.HTTP_202_ACCEPTED)
async def create_task(task_name: str):
    """
    Create and start a new background task.

    Note: We don't pass arguments via the API in this basic example.
    A more advanced implementation might accept a request body with parameters.
    Remember you can't pass python objects and need to de-/serialize them to JSON.

    Args:
        task_name: The name of the task to create, as registered in the TaskManager.

    Returns:
        The created task object.
    """
    try:
        task_manager = get_task_manager()
        task = await task_manager.create_and_run_task(task_name)
        return task
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
