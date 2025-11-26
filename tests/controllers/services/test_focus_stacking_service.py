"""Tests for the focus stacking service layer persistence behaviour."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from openscan.config.camera import CameraSettings
from openscan.config.scan import ScanSetting
from openscan.controllers.services import focus_stacking as service
from openscan.models.scan import Scan, StackingTaskStatus
from openscan.models.task import Task, TaskStatus


@pytest.fixture(name="scan")
def fixture_scan() -> Scan:
    settings = ScanSetting(
        path_method="fibonacci",
        points=10,
        min_theta=0.0,
        max_theta=170.0,
        optimize_path=True,
        optimization_algorithm="nearest_neighbor",
        focus_stacks=1,
        focus_range=(10.0, 15.0),
        image_format="jpeg",
    )
    return Scan(
        project_name="demo",
        index=1,
        created=datetime.now(),
        settings=settings,
        camera_settings=CameraSettings(),
        camera_name="cam",
    )


@pytest.fixture(autouse=True)
def patch_project_manager(monkeypatch, scan: Scan):
    project_manager = MagicMock()
    project_manager.get_scan_by_index.return_value = scan
    project_manager.save_scan_state = AsyncMock()
    monkeypatch.setattr("openscan.controllers.services.focus_stacking.get_project_manager", lambda: project_manager)
    return project_manager


@pytest.fixture(autouse=True)
def patch_task_manager(monkeypatch):
    task_manager = MagicMock()
    task_manager.create_and_run_task = AsyncMock()
    task_manager.pause_task = AsyncMock()
    task_manager.resume_task = AsyncMock()
    task_manager.cancel_task = AsyncMock()
    monkeypatch.setattr("openscan.controllers.services.focus_stacking.get_task_manager", lambda: task_manager)
    return task_manager


@pytest.mark.asyncio
async def test_start_focus_stacking_persists_task_reference(scan: Scan, patch_project_manager, patch_task_manager):
    patch_task_manager.create_and_run_task.return_value = Task(
        name="focus",
        task_type="core",
        status=TaskStatus.RUNNING,
        id="task-123",
    )

    task = await service.start_focus_stacking("demo", 1)

    patch_project_manager.get_scan_by_index.assert_called_once_with("demo", 1)
    assert scan.stacking_task_status == StackingTaskStatus(task_id="task-123", status=TaskStatus.RUNNING)
    patch_project_manager.save_scan_state.assert_awaited_once_with(scan)
    assert task.id == "task-123"


@pytest.mark.asyncio
async def test_start_focus_stacking_returns_existing_active_task(scan: Scan, patch_project_manager, patch_task_manager):
    existing_task = Task(name="focus", task_type="core", status=TaskStatus.RUNNING, id="task-999")
    scan.stacking_task_status = StackingTaskStatus(task_id="task-999", status=TaskStatus.RUNNING)
    patch_task_manager.get_task_info.return_value = existing_task

    task = await service.start_focus_stacking("demo", 1)

    patch_task_manager.create_and_run_task.assert_not_called()
    patch_project_manager.save_scan_state.assert_not_awaited()
    assert task.id == "task-999"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "service_fn, manager_attr, expected_status",
    [
        (service.pause_focus_stacking, "pause_task", TaskStatus.PAUSED),
        (service.resume_focus_stacking, "resume_task", TaskStatus.RUNNING),
        (service.cancel_focus_stacking, "cancel_task", TaskStatus.CANCELLED),
    ],
)
async def test_state_transitions_persisted(service_fn, manager_attr, expected_status, scan: Scan, patch_project_manager, patch_task_manager):
    scan.stacking_task_status = StackingTaskStatus(task_id="task-123", status=TaskStatus.RUNNING)

    getattr(patch_task_manager, manager_attr).return_value = Task(
        name="focus",
        task_type="core",
        status=expected_status,
        id="task-123",
    )

    task = await service_fn("demo", 1)

    assert scan.stacking_task_status.status == expected_status
    patch_project_manager.save_scan_state.assert_awaited_once_with(scan)
    assert task.status == expected_status
    patch_project_manager.save_scan_state.await_count = 0


@pytest.mark.asyncio
async def test_resume_focus_stacking_without_task_returns_none(scan: Scan, patch_project_manager, patch_task_manager):
    scan.stacking_task_status = None

    result = await service.resume_focus_stacking("demo", 1)

    assert result is None
    patch_task_manager.resume_task.assert_not_called()
    patch_project_manager.save_scan_state.assert_not_awaited()
