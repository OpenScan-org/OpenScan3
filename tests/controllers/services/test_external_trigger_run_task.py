from unittest.mock import AsyncMock, patch

import pytest

from openscan_firmware.config.external_trigger_run import ExternalTriggerRunSettings
from openscan_firmware.controllers.services.external_trigger_runs import ExternalTriggerRunManager
from openscan_firmware.controllers.services.tasks.core.external_trigger_run_task import ExternalTriggerRunTask
from openscan_firmware.models.paths import PolarPoint3D
from openscan_firmware.models.task import Task


@pytest.mark.asyncio
async def test_external_trigger_run_task_generates_path_without_run_log(tmp_path) -> None:
    manager = ExternalTriggerRunManager(path=tmp_path)
    settings = ExternalTriggerRunSettings(
        points=2,
        trigger_name="external-camera",
        pre_trigger_delay_ms=10,
        post_trigger_delay_ms=20,
    )

    task_model = Task(name="external_trigger_run_task", task_type="core")
    task = ExternalTriggerRunTask(task_model)

    path_dict = {
        PolarPoint3D(theta=10.0, fi=20.0): 0,
        PolarPoint3D(theta=30.0, fi=40.0): 1,
    }

    move_to_point = AsyncMock()
    trigger_controller = AsyncMock()
    fire_trigger = AsyncMock()
    reset_trigger = AsyncMock()
    trigger_controller.trigger = fire_trigger
    trigger_controller.reset = reset_trigger

    with patch(
        "openscan_firmware.controllers.services.tasks.core.external_trigger_run_task.get_external_trigger_run_manager",
        return_value=manager,
    ), patch(
        "openscan_firmware.controllers.services.tasks.core.external_trigger_run_task.generate_scan_path",
        return_value=path_dict,
    ), patch(
        "openscan_firmware.controllers.hardware.motors.move_to_point",
        move_to_point,
    ), patch(
        "openscan_firmware.controllers.services.tasks.core.external_trigger_run_task.get_trigger_controller",
        return_value=trigger_controller,
    ):
        progress_updates = [
            progress async for progress in task.run(
                settings.model_dump(mode="json"),
                label="gpio-seq",
            )
        ]

    assert progress_updates[-1].current == 2
    assert progress_updates[-1].total == 2
    assert move_to_point.await_count == 3
    assert move_to_point.await_args_list[-1].args == (PolarPoint3D(theta=90.0, fi=90.0, r=1.0),)

    path_data = manager.get_path_data(task.id)
    assert path_data is not None
    assert path_data.task_id == task.id
    assert path_data.total_steps == 2
    assert len(path_data.points) == 2

    assert (manager.path / task.id / "run_log.json").exists() is False
    assert (manager.path / task.id / "run.json").exists() is False
    assert task_model.result == {
        "task_id": task.id,
        "path_path": str(manager.path_file(task.id)),
    }
    assert fire_trigger.await_count == 2
    fire_trigger.assert_any_await(pre_trigger_delay_ms=10, post_trigger_delay_ms=20)
    reset_trigger.assert_awaited_once()
