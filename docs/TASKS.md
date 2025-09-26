# OpenScan3 Task System

This document explains how background tasks work in OpenScan3, how they are discovered and scheduled, and how to implement your own tasks.

## Overview

- Tasks live under `app/controllers/services/tasks/`.
- The `TaskManager` is responsible for registration, scheduling, persistence, and lifecycle management of tasks.
- Tasks are Python classes inheriting from `BaseTask` and must define an explicit `task_name` in snake_case with the `_task` suffix, e.g. `scan_task`.
- Tasks are auto-discovered at application startup (see `app/main.py`) based on configuration in `settings/openscan_firmware.json`.

## Directory Structure

- Core (production) tasks: `app/controllers/services/tasks/core/`
  - `scan_task.py`: Exclusive async task (generator style) responsible for the scan workflow.
  - `crop_task.py`: Blocking non-exclusive task for simple crop detection.
- Example tasks: `app/controllers/services/tasks/examples/`
  - `demo_examples.py`: Contains multiple demo tasks such as `hello_world_async_task`, `hello_world_blocking_task`, `exclusive_demo_task`, `generator_task`, `failing_task`.
- Community tasks: `app/tasks/community/`

Legacy modules at `app/controllers/services/tasks/scan_task.py`, `.../crop_task.py`, and `.../example_tasks.py` have been removed in favor of the new structure and will raise an import error if used.

## Autodiscovery

Autodiscovery is configured in `settings/openscan_firmware.json`:

- `task_autodiscovery_enabled` (bool): Enable/disable autodiscovery at startup.
- `task_autodiscovery_namespaces` (list[str]): Python package roots to scan, e.g. `app.controllers.services.tasks`, `app.tasks.community`.
- `task_autodiscovery_include_subpackages` (bool): Recursively scan subpackages.
- `task_autodiscovery_ignore_modules` (list[str]): Basenames of modules to skip, e.g. `base_task`, `task_manager`.
- `task_autodiscovery_safe_mode` (bool): Import errors are logged and ignored instead of aborting startup.
- `task_autodiscovery_override_on_conflict` (bool): When two tasks register the same `task_name`, optionally override the existing registration.
- `task_categories_enabled` (bool): Enable validation of required core tasks.
- `task_required_core_names` (list[str]): List of required task names, e.g. `["scan_task", "crop_task"]`.

A module can opt out of autodiscovery by declaring `__openscan_autodiscover__ = False` at the module level.

## Task Class Requirements

A minimal task class looks like this:

```python
from app.controllers.services.tasks.base_task import BaseTask
from app.models.task import TaskProgress

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

The TaskManager persists task state (including arguments) to disk under an internal storage path. On startup, after successful autodiscovery, the manager restores persisted tasks via `restore_tasks_from_persistence()`.

To keep arguments persistable, prefer simple types (numbers, strings, dicts/lists) or Pydantic models that support `.model_dump()`.

## Using Tasks via API / Services

Routers should generally call the service layer instead of importing task classes directly. For scans, use `app/controllers/services/scans.py`:

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

See `app/controllers/services/tasks/examples/demo_examples.py` for multiple reference implementations: async, blocking, exclusive, generator-based, and failing tasks.
