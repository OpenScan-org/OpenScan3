# OpenScan3 Task System

This document explains how background tasks work in OpenScan3, how they are discovered and scheduled, and how to implement your own tasks.

## Overview

- Tasks live under `openscan_firmware/controllers/services/tasks/`.
- The `TaskManager` is responsible for registration, scheduling, persistence, and lifecycle management of tasks.
- Tasks are Python classes inheriting from `BaseTask` and must define an explicit `task_name` in snake_case with the `_task` suffix, e.g. `scan_task`.
- Tasks are auto-discovered at application startup (see `openscan_firmware/main.py`) based on configuration in `settings/openscan_firmware.json`.

## Directory Structure

- Core (production) tasks: `openscan_firmware/controllers/services/tasks/core/`
  - `scan_task.py`: Exclusive async task (generator style) responsible for the scan workflow.
  - `crop_task.py`: Blocking non-exclusive task for simple crop detection.
- Example tasks: `openscan_firmware/controllers/services/tasks/examples/`
  - `demo_examples.py`: Contains multiple demo tasks such as `hello_world_async_task`, `hello_world_blocking_task`, `exclusive_demo_task`, `generator_task`, `failing_task`.
- Community tasks: `openscan_firmware/tasks/community/`

External (system-wide) community tasks can also be provided outside of the repo:

- Default directory: `/var/openscan3/community-tasks`
- Override via env var: `OPENSCAN_COMMUNITY_TASKS_DIR`

External community tasks are loaded from plain `*.py` files in that directory (no package structure required).

Legacy modules at `app/controllers/services/tasks/scan_task.py`, `.../crop_task.py`, and `.../example_tasks.py` have been removed in favor of the new structure and will raise an import error if used.

## Autodiscovery

Autodiscovery is configured in `settings/openscan_firmware.json`:

- `task_autodiscovery_enabled` (bool): Enable/disable autodiscovery at startup.
- `task_autodiscovery_namespaces` (list[str]): Python package roots to scan, e.g. `openscan_firmware.controllers.services.tasks`, `openscan_firmware.tasks.community`.
- `task_autodiscovery_include_subpackages` (bool): Recursively scan subpackages.
- `task_autodiscovery_ignore_modules` (list[str]): Basenames of modules to skip, e.g. `base_task`, `task_manager`.
- The firmware enforces a fixed core set (`scan_task`, `focus_stacking_task`, `cloud_upload_task`, `cloud_download_task`). Startup fails if any are missing after discovery, so keep those names available even when overriding implementations.

A module can opt out of autodiscovery by declaring `__openscan_autodiscover__ = False` at the module level.

### Advanced override (power users only)

The firmware defaults to keeping the first task registered under a given `task_name` and will log a warning for duplicates. If you deliberately want to swap out a core task (e.g., custom `scan_task`), set `"task_autodiscovery_override_on_conflict": true` in your own firmware JSON override. Only do this if you fully control the replacement task—overwriting core tasks can break the scanner.

## Task Class Requirements

A minimal task class looks like this:

```python
from openscan_firmware.controllers.services.tasks.base_task import BaseTask
from openscan_firmware.models.task import TaskProgress

class MyCustomTask(BaseTask):
    task_name = "my_custom_task"       # must be snake_case and end with _task
    task_category = "example"          # optional but recommended: core | example | community | test
    is_exclusive = False                # exclusive tasks block all others
    is_blocking = False                 # blocking tasks run in thread pool

    async def run(self, *args, **kwargs):
        # report progress (optional but encouraged)
        self._task_model.progress = TaskProgress(current=0, total=10, message="Starting...")
        # do work...
        return "Done!"
```

Notes:
- Use lazy imports inside `run()` if you need to access hardware controllers to avoid side effects during import time.
- For blocking work, implement `def run(...)` and set `is_blocking = True`. The TaskManager will execute it in a thread pool.
- For streaming progress, implement an async generator method `async def run(...) -> AsyncGenerator[TaskProgress, None]` and `yield` progress.

## Scheduling and Concurrency

- Non-exclusive tasks can run in parallel up to a fixed limit (`MAX_CONCURRENT_NON_EXCLUSIVE_TASKS`).
- Exclusive tasks will not start if any other task is running; they are queued.
- Blocking tasks (`is_blocking=True`) do not count against the async concurrency limit and run in a dedicated thread pool.
- Scheduling logic is encapsulated in `TaskManager` and transparent to task authors.

## Persistence

The TaskManager persists task state (including arguments) to disk under an internal storage path (`data/tasks`). On startup, after successful autodiscovery, the manager restores persisted tasks via `restore_tasks_from_persistence()`.

To keep arguments persistable, prefer simple types (numbers, strings, dicts/lists) or Pydantic models that support `.model_dump()`.

## Using Tasks via API / Services

Routers should generally call the service layer instead of importing task classes directly. For scans, use `openscan_firmware/controllers/services/scans.py`:

- `start_scan(project_manager, scan, camera_controller, start_from_step=0)`
- `pause_scan(scan)`
- `resume_scan(scan)`
- `cancel_scan(scan)`

These functions internally use `TaskManager` to create, control, and inspect tasks.

## Best Practices

- Keep imports side-effect free at module level (especially no hardware init). Use lazy imports inside `run()`.
- Always set an explicit `task_name` with `_task` suffix; autodiscovery enforces this.
- Add meaningful `task_category` (e.g., `core`, `example`, `community`) to improve filtering and future tooling.
- Provide helpful progress updates via `TaskProgress` for long-running tasks.
- Write tests covering your task’s behavior and integration with `TaskManager` (creation, progress, cancellation, pause/resume).

## Examples

See `openscan_firmware/controllers/services/tasks/examples/demo_examples.py` for multiple reference implementations: async, blocking, exclusive, generator-based, and failing tasks.
