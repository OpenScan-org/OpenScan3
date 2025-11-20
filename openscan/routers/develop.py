"""
Developer endpoints

These may be removed or changed at any time.
"""

import base64
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, status, Response, Query

from openscan.controllers.services.tasks.task_manager import get_task_manager
from openscan.models.task import TaskStatus, Task

from openscan.models.paths import PolarPoint3D
from openscan.controllers.hardware.motors import move_to_point

from openscan.utils.paths import paths


RELOAD_TRIGGER_FILE = Path(__file__).resolve().parents[2] / ".reload-trigger"

router = APIRouter(
    prefix="/develop",
    tags=["develop"],
    responses={404: {"description": "Not found"}},
)

@router.put("/scanner-position")
async def move_to_position(point: PolarPoint3D):
    """Move Rotor and Turntable to a polar point"""
    await move_to_point(point)


@router.post("/restart", status_code=status.HTTP_202_ACCEPTED)
async def restart_application() -> dict[str, str]:
    """Trigger a FastAPI reload by touching the sentinel file."""
    RELOAD_TRIGGER_FILE.write_text(str(time.time()), encoding="utf-8")
    return {"detail": "Reload triggered"}


@router.get("/crop_image", summary="Run crop task and return visualization image", response_class=Response)
async def crop_image(camera_name: str, threshold: int | None = Query(default=None, ge=0, le=255)) -> Response:
    """Run the crop task and return the visualization image with bounding boxes.

    Args:
        camera_name: Name of the camera controller to use.
        threshold: Optional Canny threshold passed to the analysis (tutorial uses a trackbar). If not set, defaults inside the task.

    Returns:
        Response: JPEG image showing contours, rectangles and circles as detected by the task.
    """
    task_manager = get_task_manager()

    # Start task
    task = await task_manager.create_and_run_task("crop_task", camera_name, threshold=threshold)

    # Wait for completion (default TaskManager timeout is fine for demo; can be adjusted if needed)
    try:
        final_task = await task_manager.wait_for_task(task.id, timeout=120.0)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Waiting for task failed: {e}")

    if final_task.status != TaskStatus.COMPLETED:
        detail = final_task.error or f"Task did not complete successfully (status={final_task.status})."
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=detail)

    result = final_task.result or {}
    if not isinstance(result, dict) or "image_base64" not in result:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Task result does not contain an image.")

    try:
        img_bytes = base64.b64decode(result["image_base64"])
    except Exception:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to decode image from task result.")

    return Response(content=img_bytes, media_type=result.get("mime", "image/jpeg"))



@router.post("/hello-world-async", response_model=Task)
async def hello_world_async(total_steps: int, delay: float):
    """Start the async hello world demo task."""

    task_manager = get_task_manager()

    # Updated to explicit task_name with required _task suffix
    task = await task_manager.create_and_run_task("hello_world_async_task", total_steps=total_steps, delay=delay)
    return task


@router.get("/{method}", response_model=list[paths.CartesianPoint3D])
async def get_path(method: paths.PathMethod, points: int):
    """Get a list of coordinates by path method and number of points"""
    return paths.get_path(method, points)