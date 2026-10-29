"""Service layer for scan setup analysis tasks."""
from __future__ import annotations

from openscan_firmware.controllers.services.tasks.task_manager import get_task_manager
from openscan_firmware.models.scan_analysis import NormalizedCrop
from openscan_firmware.models.task import Task


async def start_scan_analysis(
    camera_name: str,
    depends_on: str | None = None,
    debug_images: bool = False,
    crop: NormalizedCrop | list[float] | None = None,
) -> Task:
    """Start a scan setup analysis task through the task manager."""
    return await get_task_manager().create_and_run_task(
        "scan_analysis_task",
        camera_name=camera_name,
        depends_on=depends_on,
        debug_images=debug_images,
        crop=crop,
    )
