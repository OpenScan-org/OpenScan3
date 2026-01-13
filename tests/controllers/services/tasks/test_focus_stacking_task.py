import asyncio
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

from openscan_firmware.controllers.services.tasks.core.focus_stacking_task import FocusStackingTask
from openscan_firmware.models.task import TaskStatus


async def wait_for_status(task_manager, task_id: str, expected_status: TaskStatus, timeout: float = 5.0):
    start = asyncio.get_event_loop().time()
    while True:
        task_model = task_manager.get_task_info(task_id)
        if task_model.status == expected_status:
            return task_model
        if asyncio.get_event_loop().time() - start > timeout:
            pytest.fail(
                f"Task {task_id} did not reach status {expected_status} within {timeout}s. "
                f"Current status: {task_model.status}"
            )
        await asyncio.sleep(0.05)


def configure_focus_stacking_task(monkeypatch, env: dict, batches: dict[int, list[str]], stack_impl=None):
    monkeypatch.setattr(
        "openscan_firmware.controllers.services.projects.get_project_manager",
        lambda: env["project_manager"],
    )
    monkeypatch.setattr(
        FocusStackingTask,
        "_find_batches",
        lambda self, scan_dir: batches,
    )
    first_batch = next(iter(batches.values())) if batches else []

    class _FakeTransform:
        def tolist(self):
            return [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]

    fake_transforms = [_FakeTransform() for _ in first_batch]
    monkeypatch.setattr(
        FocusStackingTask,
        "_calibrate_stacker",
        lambda self, scan_dir, num_batches: SimpleNamespace(transforms=fake_transforms),
    )
    if stack_impl:
        monkeypatch.setattr(FocusStackingTask, "_stack_batch", stack_impl)


def make_writing_stack_impl(record: list[Path] | None = None, blocker: dict | None = None):
    call_counter = {"value": 0}

    def _impl(self, stacker, image_paths, output_path):
        call_counter["value"] += 1
        if blocker and call_counter["value"] == 1:
            blocker["start"].set()
            blocker["release"].wait()
        path = Path(output_path)
        if record is not None:
            record.append(path)
        path.write_bytes(b"stacked")

    _impl.call_counter = call_counter
    return _impl


@pytest.mark.asyncio
async def test_focus_stacking_task_happy_path(
    monkeypatch,
    focus_task_manager,
    focus_stacking_environment,
    focus_stacking_batches,
):
    stack_records: list[Path] = []
    stack_impl = make_writing_stack_impl(record=stack_records)
    configure_focus_stacking_task(monkeypatch, focus_stacking_environment, focus_stacking_batches, stack_impl)

    project = focus_stacking_environment["project"]
    scan = focus_stacking_environment["scan"]

    task_model = await focus_task_manager.create_and_run_task(
        "focus_stacking_task",
        project.name,
        scan.index,
    )

    final_state = await wait_for_status(focus_task_manager, task_model.id, TaskStatus.COMPLETED)

    expected_outputs = [
        focus_stacking_environment["stacked_dir"] / f"stacked_scan{scan.index:02d}_{position:03d}.jpg"
        for position in sorted(focus_stacking_batches)
    ]

    assert final_state.status == TaskStatus.COMPLETED
    assert final_state.result["stacked_image_count"] == len(expected_outputs)
    assert [Path(p) for p in final_state.result["output_paths"]] == expected_outputs
    assert stack_impl.call_counter["value"] == len(expected_outputs)
    assert all(path.read_bytes() == b"stacked" for path in expected_outputs)


@pytest.mark.asyncio
async def test_focus_stacking_task_pause_and_resume(
    monkeypatch,
    focus_task_manager,
    focus_stacking_environment,
    focus_stacking_batches,
):
    blocker = {"start": Event(), "release": Event()}
    stack_impl = make_writing_stack_impl(blocker=blocker)
    configure_focus_stacking_task(monkeypatch, focus_stacking_environment, focus_stacking_batches, stack_impl)

    project = focus_stacking_environment["project"]
    scan = focus_stacking_environment["scan"]

    task_model = await focus_task_manager.create_and_run_task(
        "focus_stacking_task",
        project.name,
        scan.index,
    )

    await wait_for_status(focus_task_manager, task_model.id, TaskStatus.RUNNING)

    while not blocker["start"].wait(0.01):
        await asyncio.sleep(0.01)

    paused_state = await focus_task_manager.pause_task(task_model.id)
    assert paused_state.status == TaskStatus.PAUSED

    blocker["release"].set()
    await asyncio.sleep(0.05)
    assert focus_task_manager.get_task_info(task_model.id).status == TaskStatus.PAUSED

    resumed_state = await focus_task_manager.resume_task(task_model.id)
    assert resumed_state.status == TaskStatus.RUNNING

    final_state = await wait_for_status(focus_task_manager, task_model.id, TaskStatus.COMPLETED)
    assert final_state.status == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_focus_stacking_task_cancel(
    monkeypatch,
    focus_task_manager,
    focus_stacking_environment,
    focus_stacking_batches,
):
    blocker = {"start": Event(), "release": Event()}
    stack_impl = make_writing_stack_impl(blocker=blocker)
    configure_focus_stacking_task(monkeypatch, focus_stacking_environment, focus_stacking_batches, stack_impl)

    project = focus_stacking_environment["project"]
    scan = focus_stacking_environment["scan"]

    task_model = await focus_task_manager.create_and_run_task(
        "focus_stacking_task",
        project.name,
        scan.index,
    )

    await wait_for_status(focus_task_manager, task_model.id, TaskStatus.RUNNING)

    while not blocker["start"].wait(0.01):
        await asyncio.sleep(0.01)

    cancelled_state = await focus_task_manager.cancel_task(task_model.id)
    assert cancelled_state.status == TaskStatus.CANCELLED
    blocker["release"].set()

    final_state = await wait_for_status(focus_task_manager, task_model.id, TaskStatus.CANCELLED)
    assert final_state.status == TaskStatus.CANCELLED
    assert final_state.result is None


@pytest.mark.asyncio
async def test_focus_stacking_task_overwrites_existing_outputs(
    monkeypatch,
    focus_task_manager,
    focus_stacking_environment,
    focus_stacking_batches,
):
    stack_impl = make_writing_stack_impl()
    configure_focus_stacking_task(monkeypatch, focus_stacking_environment, focus_stacking_batches, stack_impl)

    project = focus_stacking_environment["project"]
    scan = focus_stacking_environment["scan"]

    existing_files = [
        focus_stacking_environment["stacked_dir"] / f"stacked_scan{scan.index:02d}_{position:03d}.jpg"
        for position in sorted(focus_stacking_batches)
    ]
    for file_path in existing_files:
        file_path.write_bytes(b"old")

    task_model = await focus_task_manager.create_and_run_task(
        "focus_stacking_task",
        project.name,
        scan.index,
    )

    final_state = await wait_for_status(focus_task_manager, task_model.id, TaskStatus.COMPLETED)

    for file_path in existing_files:
        assert file_path.read_bytes() == b"stacked"
    assert final_state.status == TaskStatus.COMPLETED
    assert final_state.result["stacked_image_count"] == len(existing_files)


@pytest.mark.asyncio
async def test_focus_stacking_task_completes_missing_outputs(
    monkeypatch,
    focus_task_manager,
    focus_stacking_environment,
    focus_stacking_batches,
):
    stack_impl = make_writing_stack_impl()
    configure_focus_stacking_task(monkeypatch, focus_stacking_environment, focus_stacking_batches, stack_impl)

    project = focus_stacking_environment["project"]
    scan = focus_stacking_environment["scan"]

    partial_output = focus_stacking_environment["stacked_dir"] / f"stacked_scan{scan.index:02d}_001.jpg"
    partial_output.write_bytes(b"old")

    task_model = await focus_task_manager.create_and_run_task(
        "focus_stacking_task",
        project.name,
        scan.index,
    )

    final_state = await wait_for_status(focus_task_manager, task_model.id, TaskStatus.COMPLETED)

    expected_files = [
        focus_stacking_environment["stacked_dir"] / f"stacked_scan{scan.index:02d}_{position:03d}.jpg"
        for position in sorted(focus_stacking_batches)
    ]
    for file_path in expected_files:
        assert file_path.read_bytes() == b"stacked"
    assert final_state.result["stacked_image_count"] == len(expected_files)


@pytest.mark.asyncio
async def test_focus_stacking_task_fails_on_stack_error(
    monkeypatch,
    focus_task_manager,
    focus_stacking_environment,
    focus_stacking_batches,
):
    def failing_stack_batch(self, stacker, image_paths, output_path):
        raise RuntimeError("stack failure")

    configure_focus_stacking_task(
        monkeypatch,
        focus_stacking_environment,
        focus_stacking_batches,
        failing_stack_batch,
    )

    project = focus_stacking_environment["project"]
    scan = focus_stacking_environment["scan"]

    task_model = await focus_task_manager.create_and_run_task(
        "focus_stacking_task",
        project.name,
        scan.index,
    )

    final_state = await wait_for_status(focus_task_manager, task_model.id, TaskStatus.ERROR)
    assert final_state.status == TaskStatus.ERROR
    assert "stack failure" in final_state.error
