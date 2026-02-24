"""
Scan Service

This module provides functions to interact with scan tasks. It acts as a bridge
between the application's business logic (e.g., API routers) and the TaskManager,
which handles the actual execution and state management of the scan processes.

All scan operations (start, pause, resume, cancel) are delegated to the TaskManager
using the task_id associated with a scan.
"""
import logging
from typing import Optional

from openscan_firmware.controllers.hardware.cameras.camera import CameraController
from openscan_firmware.controllers.services.projects import ProjectManager, get_project_manager
from openscan_firmware.controllers.services.tasks.task_manager import get_task_manager
from openscan_firmware.models.scan import Scan
from openscan_firmware.models.task import Task, TaskStatus

logger = logging.getLogger(__name__)


async def start_scan(
    project_manager: ProjectManager,
    scan: Scan,
    camera_controller: CameraController,
    start_from_step: int = 0,
) -> Task:
    """
    Creates and starts a new scan task with simplified arguments.

    This function requests the TaskManager to create a `scan_task`, saves the
    task_id to the scan object, and starts the task.
    The task will resolve its own dependencies using service locators.

    Args:
        project_manager: The project manager instance (used for saving scan state).
        scan: The scan object to be executed.
        camera_controller: The camera controller for validation.
        start_from_step: The step to resume the scan from.

    Returns:
        The created Task object.
    """
    task_manager = get_task_manager()

    # Validate that the camera matches the scan's expected camera
    if scan.camera_name and scan.camera_name != camera_controller.camera.name:
        raise ValueError(f"Camera mismatch: scan expects '{scan.camera_name}', got '{camera_controller.camera.name}'")

    # If the scan already has a task_id, check its status.
    # This prevents creating a new task for a scan that is already running, paused, etc.
    if scan.task_id:
        existing_task = task_manager.get_task_info(scan.task_id)
        restartable_statuses = {
            TaskStatus.COMPLETED,
            TaskStatus.CANCELLED,
            TaskStatus.ERROR,
            TaskStatus.INTERRUPTED,
        }

        if existing_task:
            if existing_task.status not in restartable_statuses:
                logger.warning(
                    f"Scan {scan.index} already has an active task {scan.task_id} with status {existing_task.status}. "
                    f"Cannot start a new one."
                )
                return existing_task

            if existing_task.status == TaskStatus.INTERRUPTED:
                logger.info(
                    "Restarting interrupted scan %s (task %s) from step %s.",
                    scan.index,
                    scan.task_id,
                    start_from_step,
                )

            # Remove the stale terminal task so the TaskManager list reflects only the new run
            await task_manager.delete_task(existing_task.id)
            scan.task_id = None

    task_name = "scan_task"
    task = await task_manager.create_and_run_task(
        task_name,
        scan, start_from_step
    )

    # Save the task_id in the scan object for future reference
    scan.task_id = task.id
    await project_manager.save_scan_state(scan)
    logger.info(f"Started scan {scan.index} for project '{scan.project_name}' with task_id {task.id}")

    return task


async def pause_scan(scan: Scan) -> Optional[Task]:
    """
    Pauses a running scan task.

    Args:
        scan: The scan object whose task should be paused.

    Returns:
        The updated Task object if found and paused, otherwise None.
    """
    if not scan.task_id:
        logger.warning(f"Cannot pause scan {scan.index}: no associated task_id.")
        return None

    task_manager = get_task_manager()
    scan.status = TaskStatus.PAUSED
    project_manager = get_project_manager()
    await project_manager.save_scan_state(scan)
    return await task_manager.pause_task(scan.task_id)


async def resume_scan(scan: Scan) -> Optional[Task]:
    """
    Resumes a paused scan task.

    Args:
        scan: The scan object whose task should be resumed.

    Returns:
        The updated Task object if found and resumed, otherwise None.
    """
    if not scan.task_id:
        logger.warning(f"Cannot resume scan {scan.index}: no associated task_id.")
        return None

    task_manager = get_task_manager()
    task = await task_manager.resume_task(scan.task_id)
    scan.status = TaskStatus.RUNNING
    project_manager = get_project_manager()
    await project_manager.save_scan_state(scan)
    return task


async def cancel_scan(scan: Scan) -> Optional[Task]:
    """
    Cancels a running or paused scan task.

    Args:
        scan: The scan object whose task should be cancelled.

    Returns:
        The updated Task object if found and cancelled, otherwise None.
    """
    if not scan.task_id:
        logger.warning(f"Cannot cancel scan {scan.index}: no associated task_id.")
        return None

    task_manager = get_task_manager()
    scan.status = TaskStatus.CANCELLED
    project_manager = get_project_manager()
    await project_manager.save_scan_state(scan)
    return await task_manager.cancel_task(scan.task_id)