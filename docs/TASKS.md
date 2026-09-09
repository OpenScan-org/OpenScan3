# OpenScan3 Task System

This document explains how background tasks work in OpenScan3, how they are discovered and scheduled, and how to implement your own tasks.

## Overview

- Tasks live under `openscan_firmware/controllers/services/tasks/`.
- The `TaskManager` is responsible for registration, scheduling, persistence, and lifecycle management of tasks.
- Tasks are Python classes inheriting from `BaseTask` and must define an explicit `task_name` in snake_case with the `_task` suffix, e.g. `scan_task`.
- Tasks are auto-discovered at application startup when `OPENSCAN_TASK_AUTODISCOVERY=1` is set; production images keep autodiscovery disabled.

## Directory Structure

- Core (production) tasks: `openscan_firmware/controllers/services/tasks/core/`
  - `scan_task.py`: Exclusive async task (generator style) responsible for the scan workflow.
  - `crop_task.py`: Blocking non-exclusive task for simple crop detection.
- Example tasks: `openscan_firmware/controllers/services/tasks/examples/`
  - `demo_examples.py`: Contains multiple demo tasks such as `hello_world_progress_task`, `hello_world_blocking_task`, `exclusive_demo_task`, `failing_task`.
- Community tasks: `openscan_firmware/tasks/community/`

External (system-wide) community tasks can also be provided outside of the repo:

- Default directory: `/var/openscan3/community-tasks`
- Override via env var: `OPENSCAN_COMMUNITY_TASKS_DIR`

External community tasks are loaded from plain `*.py` files in that directory (no package structure required).

Legacy modules at `app/controllers/services/tasks/scan_task.py`, `.../crop_task.py`, and `.../example_tasks.py` have been removed in favor of the new structure and will raise an import error if used.

## Autodiscovery

Autodiscovery is off by default for beta/end-user images. Set
`OPENSCAN_TASK_AUTODISCOVERY=1` in the environment to enable it (optionally combine with
`OPENSCAN_TASK_OVERRIDE_ON_CONFLICT=1` if you need to overwrite existing core tasks).
When enabled, the `TaskManager` scans `openscan_firmware.controllers.services.tasks` and
`openscan_firmware.tasks.community`, recurses into subpackages, and ignores helper
modules such as `base_task`, `task_manager`, and everything under `examples*`. Naming
checks (`task_name` must be snake_case ending in `_task`) and missing-name failures are
hardcoded policies.

The firmware's static core registry is the source of truth for all built-in
tasks. Startup fails if any built-in task is missing after discovery, so keep
those names available even when overriding implementations.

A module can opt out of autodiscovery by declaring `__openscan_autodiscover__ = False` at the module level.

### Advanced override (power users only)

The firmware defaults to keeping the first task registered under a given `task_name` and will log a warning for duplicates. If you deliberately want to swap out a core task (e.g., custom `scan_task`), export `OPENSCAN_TASK_OVERRIDE_ON_CONFLICT=1` together mit `OPENSCAN_TASK_AUTODISCOVERY=1`. Nur einschalten, wenn du die Ersatz-Implementierung komplett kontrollierst – ein falsch überschriebenes Core-Task bricht den Scanner.

## Tinkerer Workflow: External Tasks with Autodiscovery

This workflow is for experimenting with a trusted custom task on one
administered scanner. The task is installed directly on the device and remains
separate from the main firmware. It is convenient for prototypes and local
automation, but it is not the way to add a permanent supported feature.

This workflow deliberately enables the execution of locally installed Python
code. Only install task files whose complete contents you trust. Do not expose
task installation or the autodiscovery switch through an unauthenticated API or
frontend.

The following example assumes that you already provisioned an administrator
account on the Raspberry Pi and can connect over SSH.

### 1. Create the task file

Connect to the scanner and create the external task directory:

```bash
ssh <username>@openscan.local
sudo install -d -o openscan -g openscan -m 0750 \
  /var/openscan3/community-tasks
sudoedit /var/openscan3/community-tasks/hello_world_task.py
```

Add this minimal task:

```python
from openscan_firmware.controllers.services.tasks.base_task import BaseTask
from openscan_firmware.models.task import TaskProgress


class HelloWorldTask(BaseTask):
    task_name = "hello_world_task"
    task_category = "community"
    is_exclusive = False
    is_blocking = False

    async def run(self, name: str = "world"):
        self._task_model.progress = TaskProgress(
            current=0,
            total=1,
            message="Starting",
        )

        message = f"Hello, {name}!"

        self._task_model.progress = TaskProgress(
            current=1,
            total=1,
            message="Finished",
        )
        return {"message": message}
```

Make the file readable by the `openscan` service account:

```bash
sudo chown openscan:openscan \
  /var/openscan3/community-tasks/hello_world_task.py
sudo chmod 0640 \
  /var/openscan3/community-tasks/hello_world_task.py
```

External discovery reads plain top-level `*.py` files from this directory.
Files beginning with `__` are ignored. A package directory or `__init__.py` is
not required.

### 2. Enable autodiscovery locally

Create a systemd override:

```bash
sudo systemctl edit openscan3.service
```

Enter:

```ini
[Service]
Environment="OPENSCAN_TASK_AUTODISCOVERY=1"
```

Save the editor and restart the firmware:

```bash
sudo systemctl restart openscan3.service
```

Autodiscovery only runs during firmware startup. Adding or changing a task file
therefore requires another restart.

Do not enable `OPENSCAN_TASK_OVERRIDE_ON_CONFLICT` for an ordinary custom task.
It is unnecessary when the new `task_name` is unique and would allow external
files to replace core task implementations.

### 3. Verify registration

Inspect the service log:

```bash
sudo journalctl -u openscan3.service -b -n 100 --no-pager
```

A successful startup contains a message similar to:

```text
Task 'hello_world_task' (...) registered via autodiscovery.
```

If the task is missing, check that:

- the file ends in `.py` and is directly inside the community task directory;
- the service account can read the file;
- the class inherits from `BaseTask`;
- `task_name` is explicit, uses snake_case, and ends in `_task`;
- importing the file does not raise an exception; and
- `OPENSCAN_TASK_AUTODISCOVERY=1` appears in the service environment.

The effective environment can be inspected with:

```bash
sudo systemctl show openscan3.service --property=Environment
```

### 4. Run the task

Start the task through the task API:

```bash
curl --request POST \
  http://openscan.local/api/latest/tasks/hello_world_task \
  --header 'Content-Type: application/json' \
  --data '{"kwargs":{"name":"OpenScan"}}'
```

The API returns a task model with an `id`. Use that ID to inspect its state:

```bash
curl http://openscan.local/api/latest/tasks/<task-id>
```

Task parameters come from the request body's `args` and `kwargs` fields and are
passed to the task's `run()` method. Registration makes a task available to the
scheduler; it does not automatically start the task.

### 5. Disable external task loading

Open the same override again:

```bash
sudo systemctl edit openscan3.service
```

Change the flag to:

```ini
[Service]
Environment="OPENSCAN_TASK_AUTODISCOVERY=0"
```

Then restart the service:

```bash
sudo systemctl restart openscan3.service
```

The Python file remains on disk but is no longer imported. Setting the flag
explicitly to `0` avoids accidentally removing unrelated local service
overrides.

## Firmware Developer Workflow: Permanent Integration

Use this workflow when a task should become a maintained part of OpenScan,
survive firmware updates, and be available to other users.

### 1. Discuss the task with the maintainers

Before adding a permanent task, discuss it with the OpenScan maintainers. This
helps decide:

- whether it should be a background task at all;
- its stable `task_name`;
- whether it must run exclusively;
- how users or other firmware features will start it; and
- whether it introduces new dependencies or hardware requirements.

This avoids committing to a public task name or behavior that will be difficult
to change later.

### 2. Add the task class

Put the implementation under:

```text
openscan_firmware/controllers/services/tasks/core/
```

Use the `BaseTask` contract described below, including an explicit stable
`task_name`. Importing the file must not initialize hardware, open network
connections, or start other work. Do that inside `run()` instead.

### 3. Add it to the built-in registry

Import the class in
`openscan_firmware/controllers/services/tasks/core/registry.py` and add one
class to `BUILTIN_TASKS`:

```python
from openscan_firmware.controllers.services.tasks.core.hello_world_task import (
    HelloWorldTask,
)

BUILTIN_TASKS = (
    # Existing built-in task classes...
    HelloWorldTask,
)
```

The registry reads the name from `HelloWorldTask.task_name`, so it is declared
only once. No JSON entry or second list of task names is required.

### 4. Make the task available where it is needed

Experimental and custom tasks can be started through
`POST /tasks/{task_name}`. This generic endpoint only creates the task; it does
not persist references on domain objects such as scans or projects and does
not perform domain-specific validation.

Tasks owned by a user-facing workflow must be started through that workflow's
specialized endpoint. If another firmware feature needs to start a task, add a
small service function for that feature:

```python
from openscan_firmware.controllers.services.tasks.task_manager import (
    get_task_manager,
)


async def start_hello_world(name: str):
    return await get_task_manager().create_and_run_task(
        "hello_world_task",
        name=name,
    )
```

Call the function instead of creating the task class directly. A new dedicated
API endpoint is only needed if the task is part of a larger user-facing
workflow.

### 5. Add tests

A permanent task should have tests for:

- its normal result and important error cases;
- arguments passed to `run()`;
- cancellation or pause behavior, if supported; and
- registration through `BUILTIN_TASKS`.

Place task-specific tests under `tests/controllers/services/tasks/` where
possible.

Run at least:

```bash
.venv/bin/pytest -q \
  tests/controllers/services/test_task_autodiscovery.py \
  tests/controllers/services/test_task_manager.py
```

When moving a prototype into the firmware, remove its external copy from
`/var/openscan3/community-tasks` to avoid two tasks with the same name.

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
