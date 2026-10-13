from unittest.mock import AsyncMock, MagicMock

import pytest

from openscan_firmware.controllers.services import scan_analysis as service
from openscan_firmware.models.task import Task, TaskStatus


@pytest.mark.asyncio
async def test_start_scan_analysis_delegates_to_task_manager(monkeypatch):
    task_manager = MagicMock()
    task_manager.create_and_run_task = AsyncMock(
        return_value=Task(
            name="scan_analysis_task",
            task_type="scan_analysis_task",
            status=TaskStatus.RUNNING,
            id="task-123",
        )
    )
    monkeypatch.setattr(service, "get_task_manager", lambda: task_manager)

    task = await service.start_scan_analysis(
        camera_name="camera0",
        depends_on="task-prerequisite",
    )

    task_manager.create_and_run_task.assert_awaited_once_with(
        "scan_analysis_task",
        camera_name="camera0",
        depends_on="task-prerequisite",
    )
    assert task.id == "task-123"
