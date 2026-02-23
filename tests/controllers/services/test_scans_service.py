import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import openscan_firmware.controllers.services.scans as scans
from openscan_firmware.models.scan import Scan
from openscan_firmware.models.task import Task, TaskStatus


@pytest.mark.asyncio
async def test_start_scan_restarts_interrupted_task(sample_scan_model: Scan) -> None:
    scan = sample_scan_model
    scan.task_id = "task-interrupted"
    scan.camera_name = "mock-cam"

    camera_controller = MagicMock()
    camera_controller.camera = MagicMock()
    camera_controller.camera.name = "mock-cam"

    project_manager = MagicMock()
    project_manager.save_scan_state = AsyncMock()

    existing_task = Task(name="scan_task", task_type="core", status=TaskStatus.INTERRUPTED, id="task-interrupted")
    new_task = Task(name="scan_task", task_type="core", status=TaskStatus.RUNNING, id="task-new")

    task_manager_mock = MagicMock()
    task_manager_mock.get_task_info.return_value = existing_task
    task_manager_mock.create_and_run_task = AsyncMock(return_value=new_task)

    with patch("openscan_firmware.controllers.services.scans.get_task_manager", return_value=task_manager_mock):
        result = await scans.start_scan(project_manager, scan, camera_controller, start_from_step=3)

    assert result is new_task
    task_manager_mock.create_and_run_task.assert_awaited_once_with("scan_task", scan, 3)
    assert scan.task_id == new_task.id
    project_manager.save_scan_state.assert_awaited_once_with(scan)


@pytest.mark.asyncio
async def test_pause_scan_updates_status_and_persists(sample_scan_model: Scan) -> None:
    scan = sample_scan_model
    scan.task_id = "task-id"

    paused_task = Task(name="scan_task", task_type="core", status=TaskStatus.PAUSED)

    task_manager_mock = MagicMock()
    task_manager_mock.pause_task = AsyncMock(return_value=paused_task)

    project_manager_mock = MagicMock()
    project_manager_mock.save_scan_state = AsyncMock()

    with patch("openscan_firmware.controllers.services.scans.get_task_manager", return_value=task_manager_mock), \
         patch("openscan_firmware.controllers.services.scans.get_project_manager", return_value=project_manager_mock):
        result = await scans.pause_scan(scan)

    assert result is paused_task
    assert scan.status == TaskStatus.PAUSED
    task_manager_mock.pause_task.assert_awaited_once_with(scan.task_id)
    project_manager_mock.save_scan_state.assert_awaited_once_with(scan)


@pytest.mark.asyncio
async def test_pause_scan_without_task_id_returns_none(sample_scan_model: Scan) -> None:
    scan = sample_scan_model
    scan.task_id = None

    with patch("openscan_firmware.controllers.services.scans.get_task_manager") as tm_patch, \
         patch("openscan_firmware.controllers.services.scans.get_project_manager") as pm_patch:
        result = await scans.pause_scan(scan)

    assert result is None
    tm_patch.assert_not_called()
    pm_patch.assert_not_called()


@pytest.mark.asyncio
async def test_pause_scan_error_still_persists_state(sample_scan_model: Scan) -> None:
    scan = sample_scan_model
    scan.task_id = "task-id"

    task_manager_mock = MagicMock()
    task_manager_mock.pause_task = AsyncMock(side_effect=RuntimeError("pause failed"))

    project_manager_mock = MagicMock()
    project_manager_mock.save_scan_state = AsyncMock()

    with patch("openscan_firmware.controllers.services.scans.get_task_manager", return_value=task_manager_mock), \
         patch("openscan_firmware.controllers.services.scans.get_project_manager", return_value=project_manager_mock):
        with pytest.raises(RuntimeError, match="pause failed"):
            await scans.pause_scan(scan)

    assert scan.status == TaskStatus.PAUSED
    project_manager_mock.save_scan_state.assert_awaited_once_with(scan)


@pytest.mark.asyncio
async def test_resume_scan_updates_status_and_persists(sample_scan_model: Scan) -> None:
    scan = sample_scan_model
    scan.task_id = "task-id"

    resumed_task = Task(name="scan_task", task_type="core", status=TaskStatus.RUNNING)

    task_manager_mock = MagicMock()
    task_manager_mock.resume_task = AsyncMock(return_value=resumed_task)

    project_manager_mock = MagicMock()
    project_manager_mock.save_scan_state = AsyncMock()

    with patch("openscan_firmware.controllers.services.scans.get_task_manager", return_value=task_manager_mock), \
         patch("openscan_firmware.controllers.services.scans.get_project_manager", return_value=project_manager_mock):
        result = await scans.resume_scan(scan)

    assert result is resumed_task
    assert scan.status == TaskStatus.RUNNING
    task_manager_mock.resume_task.assert_awaited_once_with(scan.task_id)
    project_manager_mock.save_scan_state.assert_awaited_once_with(scan)


@pytest.mark.asyncio
async def test_resume_scan_without_task_id_returns_none(sample_scan_model: Scan) -> None:
    scan = sample_scan_model
    scan.task_id = None

    with patch("openscan_firmware.controllers.services.scans.get_task_manager") as tm_patch:
        result = await scans.resume_scan(scan)

    assert result is None
    tm_patch.assert_not_called()


@pytest.mark.asyncio
async def test_resume_scan_error_does_not_persist_state(sample_scan_model: Scan) -> None:
    scan = sample_scan_model
    scan.task_id = "task-id"

    task_manager_mock = MagicMock()
    task_manager_mock.resume_task = AsyncMock(side_effect=RuntimeError("resume failed"))

    with patch("openscan_firmware.controllers.services.scans.get_task_manager", return_value=task_manager_mock), \
         patch("openscan_firmware.controllers.services.scans.get_project_manager") as project_manager_patch:
        with pytest.raises(RuntimeError, match="resume failed"):
            await scans.resume_scan(scan)

    assert scan.status == TaskStatus.PENDING
    project_manager_patch.assert_not_called()


@pytest.mark.asyncio
async def test_cancel_scan_updates_status_and_persists(sample_scan_model: Scan) -> None:
    scan = sample_scan_model
    scan.task_id = "task-id"

    cancelled_task = Task(name="scan_task", task_type="core", status=TaskStatus.CANCELLED)

    task_manager_mock = MagicMock()
    task_manager_mock.cancel_task = AsyncMock(return_value=cancelled_task)

    project_manager_mock = MagicMock()
    project_manager_mock.save_scan_state = AsyncMock()

    with patch("openscan_firmware.controllers.services.scans.get_task_manager", return_value=task_manager_mock), \
         patch("openscan_firmware.controllers.services.scans.get_project_manager", return_value=project_manager_mock):
        result = await scans.cancel_scan(scan)

    assert result is cancelled_task
    assert scan.status == TaskStatus.CANCELLED
    task_manager_mock.cancel_task.assert_awaited_once_with(scan.task_id)
    project_manager_mock.save_scan_state.assert_awaited_once_with(scan)


@pytest.mark.asyncio
async def test_cancel_scan_without_task_id_returns_none(sample_scan_model: Scan) -> None:
    scan = sample_scan_model
    scan.task_id = None

    with patch("openscan_firmware.controllers.services.scans.get_task_manager") as tm_patch:
        result = await scans.cancel_scan(scan)

    assert result is None
    tm_patch.assert_not_called()


@pytest.mark.asyncio
async def test_cancel_scan_error_still_persists_state(sample_scan_model: Scan) -> None:
    scan = sample_scan_model
    scan.task_id = "task-id"

    task_manager_mock = MagicMock()
    task_manager_mock.cancel_task = AsyncMock(side_effect=RuntimeError("cancel failed"))

    project_manager_mock = MagicMock()
    project_manager_mock.save_scan_state = AsyncMock()

    with patch("openscan_firmware.controllers.services.scans.get_task_manager", return_value=task_manager_mock), \
         patch("openscan_firmware.controllers.services.scans.get_project_manager", return_value=project_manager_mock):
        with pytest.raises(RuntimeError, match="cancel failed"):
            await scans.cancel_scan(scan)

    assert scan.status == TaskStatus.CANCELLED
    project_manager_mock.save_scan_state.assert_awaited_once_with(scan)
