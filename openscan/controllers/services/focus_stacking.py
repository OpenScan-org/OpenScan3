"""Focus stacking task service layer."""
from __future__ import annotations

import logging
from typing import Optional

from openscan.controllers.services.projects import get_project_manager
from openscan.controllers.services.tasks.task_manager import get_task_manager
from openscan.models.scan import StackingTaskStatus
from openscan.models.task import Task, TaskStatus


logger = logging.getLogger(__name__)

_ACTIVE_STATUSES = {
    TaskStatus.PENDING,
    TaskStatus.RUNNING,
    TaskStatus.PAUSED,
}


async def start_focus_stacking(project_name: str, scan_index: int) -> Task:
    """Start a focus stacking task and persist the task reference on the scan.

    Args:
        project_name: Name of the project containing the scan.
        scan_index: Index of the scan to process.

    Returns:
        The Task representing the focus stacking job.
    """

    task_manager = get_task_manager()
    project_manager = get_project_manager()

    scan = project_manager.get_scan_by_index(project_name, scan_index)
    if scan is None:
        raise ValueError(f"Scan {scan_index} not found in project '{project_name}'")

    existing = scan.stacking_task_status
    if existing and existing.task_id:
        existing_task = task_manager.get_task_info(existing.task_id)
        if existing_task and existing_task.status in _ACTIVE_STATUSES:
            logger.warning(
                "Focus stacking already active for project '%s' scan %s (task %s, status=%s)",
                project_name,
                scan_index,
                existing_task.id,
                existing_task.status,
            )
            return existing_task

    task = await task_manager.create_and_run_task(
        "focus_stacking_task",
        project_name,
        scan_index,
    )

    scan.stacking_task_status = StackingTaskStatus(task_id=task.id, status=task.status)
    await project_manager.save_scan_state(scan)
    return task


async def pause_focus_stacking(project_name: str, scan_index: int) -> Optional[Task]:
    """Pause an active focus stacking task and update the scan state."""

    task_manager = get_task_manager()
    project_manager = get_project_manager()

    scan = project_manager.get_scan_by_index(project_name, scan_index)
    if scan is None:
        raise ValueError(f"Scan {scan_index} not found in project '{project_name}'")

    if not scan.stacking_task_status or not scan.stacking_task_status.task_id:
        logger.warning("Cannot pause focus stacking for scan %s: no active task", scan_index)
        return None

    task = await task_manager.pause_task(scan.stacking_task_status.task_id)
    scan.stacking_task_status.status = task.status
    await project_manager.save_scan_state(scan)
    return task


async def resume_focus_stacking(project_name: str, scan_index: int) -> Optional[Task]:
    """Resume a paused focus stacking task and update the scan state."""

    task_manager = get_task_manager()
    project_manager = get_project_manager()

    scan = project_manager.get_scan_by_index(project_name, scan_index)
    if scan is None:
        raise ValueError(f"Scan {scan_index} not found in project '{project_name}'")

    if not scan.stacking_task_status or not scan.stacking_task_status.task_id:
        logger.warning("Cannot resume focus stacking for scan %s: no paused task", scan_index)
        return None

    task = await task_manager.resume_task(scan.stacking_task_status.task_id)
    scan.stacking_task_status.status = task.status
    await project_manager.save_scan_state(scan)
    return task


async def cancel_focus_stacking(project_name: str, scan_index: int) -> Optional[Task]:
    """Cancel an active focus stacking task and update the scan state."""

    task_manager = get_task_manager()
    project_manager = get_project_manager()

    scan = project_manager.get_scan_by_index(project_name, scan_index)
    if scan is None:
        raise ValueError(f"Scan {scan_index} not found in project '{project_name}'")

    if not scan.stacking_task_status or not scan.stacking_task_status.task_id:
        logger.warning("Cannot cancel focus stacking for scan %s: no active task", scan_index)
        return None

    task = await task_manager.cancel_task(scan.stacking_task_status.task_id)
    scan.stacking_task_status.status = task.status
    await project_manager.save_scan_state(scan)
    return task
