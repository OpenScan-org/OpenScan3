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

from app.controllers.hardware.cameras.camera import CameraController
from app.controllers.services.projects import ProjectManager
from app.controllers.services.tasks.scan_task import ScanTask
from app.controllers.services.tasks.task_manager import get_task_manager
from app.models.scan import Scan, ScanStatus
from app.models.task import Task, TaskStatus

logger = logging.getLogger(__name__)


async def start_scan(
    project_manager: ProjectManager,
    scan: Scan,
    camera_controller: CameraController,
    start_from_step: int = 0,
) -> Task:
    """
    Creates and starts a new scan task.

    This function initializes a ScanTask, creates a corresponding task in the
    TaskManager, saves the task_id to the scan object, and starts the task.

    Args:
        project_manager: The project manager instance.
        scan: The scan object to be executed.
        camera_controller: The camera controller for the scan.
        start_from_step: The step to resume the scan from.

    Returns:
        The created Task object.
    """
    task_manager = get_task_manager()

    # If the scan already has a task_id, check its status.
    # This prevents creating a new task for a scan that is already running, paused, etc.
    if scan.task_id:
        existing_task = task_manager.get_task_info(scan.task_id)
        if existing_task and existing_task.status not in [
            TaskStatus.COMPLETED,
            TaskStatus.CANCELLED,
            TaskStatus.ERROR,
        ]:
            logger.warning(
                f"Scan {scan.index} already has an active task {scan.task_id} with status {existing_task.status}. "
                f"Cannot start a new one."
            )
            return existing_task

    task_name = "scan_task"
    task = await task_manager.create_and_run_task(
        task_name,
        scan, camera_controller, project_manager, start_from_step
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
    scan.status = ScanStatus.PAUSED
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
    return await task_manager.resume_task(scan.task_id)


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
    scan.status = ScanStatus.CANCELLED
    return await task_manager.cancel_task(scan.task_id)