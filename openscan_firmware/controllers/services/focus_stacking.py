"""Focus stacking task service layer."""
from __future__ import annotations

import logging
from typing import Optional

from openscan_firmware.controllers.services.projects import get_project_manager
from openscan_firmware.controllers.services.tasks.task_manager import get_task_manager
from openscan_firmware.models.scan import StackingTaskStatus
from openscan_firmware.models.task import Task, TaskStatus


logger = logging.getLogger(__name__)

_ACTIVE_STATUSES = {
    TaskStatus.PENDING,
    TaskStatus.RUNNING,
    TaskStatus.PAUSED,
}


async def start_focus_stacking(
    project_name: str,
    scan_index: int,
    depends_on: str | None = None,
) -> Task:
    """Start a focus stacking task and persist the task reference on the scan.

    Args:
        project_name: Name of the project containing the scan.
        scan_index: Index of the scan to process.
        depends_on: Optional ID of a task that must complete successfully first.

    Returns:
        The Task representing the focus stacking job.
    """

    task_manager = get_task_manager()
    project_manager = get_project_manager()

    scan = project_manager.get_scan_by_index(project_name, scan_index)
    if scan is None:
        raise ValueError(f"Scan {scan_index} not found in project '{project_name}'")

    existing = scan.stacking_task_status
    replaced_task_id: str | None = None
    replacement_dependency = depends_on
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

        replaced_task_id = existing.task_id
        if replacement_dependency is None and existing_task is not None:
            replacement_dependency = existing_task.depends_on

    task_kwargs = {"depends_on": replacement_dependency} if replacement_dependency is not None else {}
    task = await task_manager.create_and_run_task(
        "focus_stacking_task",
        project_name,
        scan_index,
        **task_kwargs,
    )

    if replaced_task_id:
        await task_manager.replace_task(replaced_task_id, task.id)

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
    """Resume a paused or interrupted focus stacking task and update the scan state."""

    task_manager = get_task_manager()
    project_manager = get_project_manager()

    scan = project_manager.get_scan_by_index(project_name, scan_index)
    if scan is None:
        raise ValueError(f"Scan {scan_index} not found in project '{project_name}'")

    stacking_status = scan.stacking_task_status
    if not stacking_status:
        logger.warning("Cannot resume focus stacking for scan %s: no task", scan_index)
        return None

    if not stacking_status.task_id:
        if stacking_status.status == TaskStatus.INTERRUPTED:
            logger.info(
                "Starting interrupted focus stacking for project '%s', scan %s.",
                project_name,
                scan_index,
            )
            return await start_focus_stacking(project_name, scan_index)

        logger.warning(
            "Cannot resume focus stacking for scan %s: no task ID",
            scan_index,
        )
        return None

    task = await task_manager.resume_task(stacking_status.task_id)
    if task is None:
        if stacking_status.status == TaskStatus.INTERRUPTED:
            logger.info(
                "Recreating missing interrupted focus stacking task for project '%s', scan %s.",
                project_name,
                scan_index,
            )
            return await start_focus_stacking(project_name, scan_index)
        return None

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
