import json
from dataclasses import asdict
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openscan_firmware.config.external_trigger_run import ExternalTriggerRunSettings
from openscan_firmware.controllers.services.external_trigger_runs import (
    ExternalTriggerRunManager,
    cancel_external_trigger_run,
    get_external_trigger_task,
    list_external_trigger_tasks,
    pause_external_trigger_run,
    resume_external_trigger_run,
    start_external_trigger_run,
)
from openscan_firmware.models.paths import CartesianPoint3D, PolarPoint3D
from openscan_firmware.models.task import Task, TaskStatus


def _sample_settings() -> ExternalTriggerRunSettings:
    return ExternalTriggerRunSettings(
        points=8,
        trigger_name="external-camera",
        pre_trigger_delay_ms=10,
        post_trigger_delay_ms=20,
    )


def test_manager_save_path_data_persists_path_only(tmp_path) -> None:
    manager = ExternalTriggerRunManager(path=tmp_path)
    path_data = manager.save_path_data(
        {
            "task_id": "task-ext-0",
            "total_steps": 1,
            "points": [
                {
                    "execution_step": 0,
                    "original_step": 0,
                    "polar_coordinates": asdict(PolarPoint3D(theta=10.0, fi=20.0)),
                    "cartesian_coordinates": asdict(CartesianPoint3D(x=1.0, y=2.0, z=3.0)),
                }
            ],
        }
    )

    assert path_data.task_id == "task-ext-0"
    assert manager.path_file("task-ext-0").exists() is True
    assert (manager.path / "task-ext-0" / "run.json").exists() is False


def test_manager_get_path_data_reads_legacy_manifest_file(tmp_path) -> None:
    manager = ExternalTriggerRunManager(path=tmp_path)

    legacy_manifest = {
        "run_id": "task-ext-legacy",
        "generated_at": datetime(2026, 4, 9, 12, 0, 0).isoformat(),
        "label": "legacy-run",
        "description": "legacy manifest payload",
        "settings": _sample_settings().model_dump(mode="json"),
        "total_steps": 1,
        "points": [
            {
                "execution_step": 0,
                "original_step": 0,
                "polar_coordinates": asdict(PolarPoint3D(theta=10.0, fi=20.0)),
                "cartesian_coordinates": asdict(CartesianPoint3D(x=1.0, y=2.0, z=3.0)),
            }
        ],
    }
    (manager.path / "task-ext-legacy" / "manifest.json").parent.mkdir(parents=True, exist_ok=True)
    (manager.path / "task-ext-legacy" / "manifest.json").write_text(json.dumps(legacy_manifest, indent=2), encoding="utf-8")

    path_data = manager.get_path_data("task-ext-legacy")

    assert path_data is not None
    assert path_data.task_id == "task-ext-legacy"
    assert path_data.total_steps == 1
    assert len(path_data.points) == 1


def test_list_external_trigger_tasks_filters_and_sorts_by_created_at() -> None:
    older_task = Task(
        id="task-ext-older",
        name="external_trigger_run_task",
        task_type="external_trigger_run_task",
        created_at=datetime(2026, 4, 9, 10, 0, 0),
    )
    newer_task = Task(
        id="task-ext-newer",
        name="external_trigger_run_task",
        task_type="external_trigger_run_task",
        created_at=datetime(2026, 4, 9, 11, 0, 0),
    )
    unrelated_task = Task(
        id="task-other",
        name="scan_task",
        task_type="scan_task",
        created_at=datetime(2026, 4, 9, 12, 0, 0),
    )
    task_manager = MagicMock()
    task_manager.get_all_tasks_info.return_value = [older_task, unrelated_task, newer_task]

    with patch(
        "openscan_firmware.controllers.services.external_trigger_runs.get_task_manager",
        return_value=task_manager,
    ):
        tasks = list_external_trigger_tasks()

    assert [task.id for task in tasks] == ["task-ext-newer", "task-ext-older"]


def test_get_external_trigger_task_returns_only_matching_task_types() -> None:
    external_task = Task(
        id="task-ext-1",
        name="external_trigger_run_task",
        task_type="external_trigger_run_task",
    )
    task_manager = MagicMock()
    task_manager.get_task_info.side_effect = [external_task, Task(id="task-other", name="scan_task", task_type="scan_task")]

    with patch(
        "openscan_firmware.controllers.services.external_trigger_runs.get_task_manager",
        return_value=task_manager,
    ):
        found_task = get_external_trigger_task("task-ext-1")
        other_task = get_external_trigger_task("task-other")

    assert found_task is external_task
    assert other_task is None


@pytest.mark.asyncio
async def test_start_external_trigger_run_delegates_to_task_manager() -> None:
    task_manager = MagicMock()
    created_task = Task(
        id="task-ext-2",
        name="external_trigger_run_task",
        task_type="core",
        status=TaskStatus.RUNNING,
    )
    task_manager.create_and_run_task = AsyncMock(return_value=created_task)

    with patch(
        "openscan_firmware.controllers.services.external_trigger_runs.get_trigger_controller",
        return_value=MagicMock(),
    ), patch(
        "openscan_firmware.controllers.services.external_trigger_runs.get_task_manager",
        return_value=task_manager,
    ):
        task = await start_external_trigger_run(
            label="bench-run",
            description="test run",
            settings=_sample_settings(),
        )

    assert task is created_task
    task_manager.create_and_run_task.assert_awaited_once_with(
        "external_trigger_run_task",
        _sample_settings().model_dump(mode="json"),
        label="bench-run",
        description="test run",
        start_from_step=0,
    )


@pytest.mark.asyncio
async def test_cancel_pause_resume_delegate_to_task_manager() -> None:
    task_manager = MagicMock()
    task_manager.cancel_task = AsyncMock(
        return_value=Task(id="task-ext-3", name="external_trigger_run_task", task_type="core", status=TaskStatus.CANCELLED)
    )
    task_manager.pause_task = AsyncMock(
        return_value=Task(id="task-ext-3", name="external_trigger_run_task", task_type="core", status=TaskStatus.PAUSED)
    )
    task_manager.resume_task = AsyncMock(
        return_value=Task(id="task-ext-3", name="external_trigger_run_task", task_type="core", status=TaskStatus.RUNNING)
    )

    with patch(
        "openscan_firmware.controllers.services.external_trigger_runs.get_task_manager",
        return_value=task_manager,
    ):
        cancelled = await cancel_external_trigger_run("task-ext-3")
        paused = await pause_external_trigger_run("task-ext-3")
        resumed = await resume_external_trigger_run("task-ext-3")

    assert cancelled.status == TaskStatus.CANCELLED
    assert paused.status == TaskStatus.PAUSED
    assert resumed.status == TaskStatus.RUNNING
