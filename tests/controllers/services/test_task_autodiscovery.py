import os
import shutil
import pytest

from openscan_firmware.controllers.services.tasks.task_manager import TaskManager


@pytest.mark.asyncio
async def test_autodiscover_registers_core_tasks():
    """Autodiscovery should register at least the required core tasks.

    We only assert the presence of core tasks here to avoid coupling to demo examples.
    """
    # Clean persistence dir and reset singleton
    TaskManager._instance = None
    tm = TaskManager()

    # Ensure a clean registry
    tm._task_registry.clear()

    registered = tm.autodiscover_tasks(
        namespaces=[
            "openscan_firmware.controllers.services.tasks",
            "openscan_firmware.tasks.community",
        ],
        include_subpackages=True,
        ignore_modules={"base_task", "task_manager", "example_tasks"},
        safe_mode=True,
        override_on_conflict=False,
        require_explicit_name=True,
        raise_on_missing_name=True,
    )

    required_core = {
        "scan_task",
        "focus_stacking_task",
        "cloud_upload_task",
        "cloud_download_task",
    }

    # Core tasks must be present
    for task_name in required_core:
        assert task_name in tm._task_registry

    # The method should return the list of newly registered tasks
    for task_name in required_core:
        assert task_name in registered


@pytest.mark.asyncio
async def test_autodiscover_safe_mode_handles_import_errors():
    """Autodiscovery should not crash on import errors when safe_mode=True.

    We intentionally do NOT ignore the legacy module `example_tasks` which raises
    ImportError on import. In safe_mode, this should be logged and skipped.
    """
    TaskManager._instance = None
    tm = TaskManager()

    # Do not ignore example_tasks to force an import error inside autodiscovery
    registered = tm.autodiscover_tasks(
        namespaces=["openscan_firmware.controllers.services.tasks"],
        include_subpackages=True,
        ignore_modules={"base_task", "task_manager"},
        safe_mode=True,
        override_on_conflict=False,
        require_explicit_name=True,
        raise_on_missing_name=True,
    )

    required_core = {
        "scan_task",
        "focus_stacking_task",
        "cloud_upload_task",
        "cloud_download_task",
    }

    # Core tasks should still be discovered
    for task_name in required_core:
        assert task_name in tm._task_registry


@pytest.mark.asyncio
async def test_autodiscover_ignore_examples_package():
    """Ignoring the examples package should prevent demo tasks from registering."""
    TaskManager._instance = None
    tm = TaskManager()

    tm.autodiscover_tasks(
        namespaces=["openscan_firmware.controllers.services.tasks"],
        include_subpackages=True,
        ignore_modules={"base_task", "task_manager", "examples"},
        safe_mode=True,
        override_on_conflict=False,
        require_explicit_name=True,
        raise_on_missing_name=True,
    )

    # Demo/example tasks should not be present (including crop_task, now an example)
    assert "hello_world_async_task" not in tm._task_registry
    assert "hello_world_blocking_task" not in tm._task_registry
    assert "exclusive_demo_task" not in tm._task_registry
    assert "crop_task" not in tm._task_registry


@pytest.mark.asyncio
async def test_autodiscover_conflict_override_false():
    """When a task_name already exists and override_on_conflict=False, keep original."""
    from openscan_firmware.controllers.services.tasks.base_task import BaseTask

    class DummyTask(BaseTask):
        task_name = "scan_task"
        task_category = "test"
        is_exclusive = False
        async def run(self):
            return None

    TaskManager._instance = None
    tm = TaskManager()

    # Pre-register dummy under the same name as a core task
    tm.register_task("scan_task", DummyTask)
    original_cls = tm._task_registry["scan_task"]

    tm.autodiscover_tasks(
        namespaces=["openscan_firmware.controllers.services.tasks"],
        include_subpackages=True,
        ignore_modules={"base_task", "task_manager"},
        safe_mode=True,
        override_on_conflict=False,
        require_explicit_name=True,
        raise_on_missing_name=True,
    )

    # Registry should still point to the original dummy task
    assert tm._task_registry["scan_task"] is original_cls
