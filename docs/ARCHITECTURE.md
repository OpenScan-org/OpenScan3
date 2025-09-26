# System Architecture Overview

This document provides an overview of the system architecture for the OpenScan3 application, which is designed to control the OpenScan photogrammetry scanner using FastAPI and a Raspberry Pi.

The architecture follows in generally the MVC (Model-View-Controller) pattern slightly adapted to fit the needs of an embedded application. Note that the controllers are responsible for the actual business logic and that models and configs are separated.

## Main Components

1. **FastAPI Application**
   - Acts as the main server handling HTTP requests.
   - Provides RESTful API endpoints for controlling the scanner.

2. **OpenScan Hardware Device**
   - usually a Raspberry Pi with a custom shield for connecting various hardware components.
   - Serves as the hardware platform for the scanner.
   - Interfaces with various peripherals (cameras, motors, lights, etc.).

## FastAPI Application Structure

1. **Routers**
   - Routers in `app/routers` handle incoming API requests.

2. **Controllers**
   - Controllers in `app/controllers` implement the business logic.
   - Settings are managed at runtime within `app/controllers/settings.py`
   - (TODO: Detection and initialization of hardware will be managed by the Device Controller within `app/controllers/hardware/device.py`, which is also capable of managing configuration profiles)

   2.1. **Hardware Controllers**
      - Located in `app/controllers/hardware` control the hardware components of the scanner.
      - Abstracts the hardware details from the application logic.
      - Supports multiple hardware configurations.
      - Hardware is divided into three categories: stateful hardware (like motors and cameras), switchable hardware (like lights), and event hardware (like buttons and simple sensors).
         - HardwareControllers inherit from the according HardwareInterface class in `app/controllers/hardware/interfaces.py`.
         - HardwareControllers instantiate the settings manager in `app/controllers/settings.py` to manage and update settings.

   2.2. **Service Controllers**
      - Controllers in `app/controllers/services` handle the business logic of the scanner.
      - Examples: Managing scan projects and scan procedures.

3. **Configuration Management**
   - Located in `app/config`.
   - Manages settings for different components.

4. **Models and Data Structures**
   - Defined in `app/models`.
   - Represents the data entities and their relationships.



## Interactions

- The FastAPI application receives requests from clients and routes them to the appropriate controllers via routers.
- Controllers interact with the hardware abstraction layer to perform operations on the scanner.

This architecture is designed to be modular and extensible, allowing for easy integration of new hardware components and features.

## Background Task System

OpenScan3 uses a centralized background task system to coordinate long-running and/or hardware-critical operations such as scanning, cropping, and demo/example jobs.

- The task system is implemented under `app/controllers/services/tasks/`.
- The central orchestrator is `TaskManager` (`app/controllers/services/tasks/task_manager.py`).
- Tasks are classes that inherit from `BaseTask` and declare an explicit, snake_case `task_name` ending with `_task` (e.g., `scan_task`).
- Tasks are auto-discovered at startup using settings in `settings/openscan_firmware.json`.

### Startup Flow

During application startup (see `app/main.py` in the FastAPI lifespan handler):

1. Logging and device initialization are performed.
2. The firmware settings file `settings/openscan_firmware.json` is read.
3. `TaskManager.autodiscover_tasks()` is invoked with the configured namespaces, subpackage handling, ignore list, and safety options.
4. If `task_categories_enabled` is true, a fail-fast check validates that required core tasks (e.g., `scan_task`, `crop_task`) are present. Missing tasks raise a `RuntimeError` and abort startup.
5. After successful registration, `TaskManager.restore_tasks_from_persistence()` is called to recover previously persisted tasks.

### Task Discovery and Structure

Tasks are organized in the following locations:

- Core (production) tasks: `app/controllers/services/tasks/core/`
  - e.g., `core/scan_task.py` (exclusive, async generator), `core/crop_task.py` (blocking, non-exclusive)
- Example/demo tasks: `app/controllers/services/tasks/examples/`
  - e.g., `examples/demo_examples.py` with `hello_world_async_task` etc.
- Community tasks: `app/tasks/community/`

Modules can opt-out from autodiscovery by setting a module-level flag `__openscan_autodiscover__ = False`. A global ignore list can also be configured in the settings file.

### Concurrency & Scheduling

The `TaskManager` enforces the following semantics:

- Non-exclusive tasks can run in parallel up to a fixed limit (`MAX_CONCURRENT_NON_EXCLUSIVE_TASKS`).
- Exclusive tasks require sole access and will prevent other tasks from starting; they are queued if necessary.
- Blocking tasks (`is_blocking = True`) run in a thread pool and do not count against the async concurrency limit. Exclusive semantics still apply.
- Scheduling decisions are encapsulated in `TaskManager._can_run_task` and the internal queueing logic.

### Service Layer for Scans

The deprecated `ScanManager` was replaced by a stateless service layer in `app/controllers/services/scans.py`:

- `start_scan(project_manager, scan, camera_controller, start_from_step=0)` creates and runs a `scan_task` via the `TaskManager`.
- `pause_scan(scan)`, `resume_scan(scan)`, `cancel_scan(scan)` delegate task lifecycle operations to the `TaskManager`.

Routers (`app/routers/`) call this service layer (e.g., in `projects.py`), avoiding direct import-time coupling to task modules.

### Configuration

Autodiscovery is configured in `settings/openscan_firmware.json`. Relevant keys:

- `task_autodiscovery_enabled`: Toggle for discovery at startup.
- `task_autodiscovery_namespaces`: Python package roots to scan (e.g., `app.controllers.services.tasks`, `app.tasks.community`).
- `task_autodiscovery_include_subpackages`: Recursively include subpackages.
- `task_autodiscovery_ignore_modules`: Module base names to skip (e.g., `base_task`, `task_manager`, optionally `example_tasks`).
- `task_autodiscovery_safe_mode`: Skip modules that fail to import and log warnings instead of aborting.
- `task_autodiscovery_override_on_conflict`: Whether to overwrite an already-registered `task_name`.
- `task_categories_enabled` and `task_required_core_names`: Enable categories and enforce presence of critical tasks (`scan_task`, `crop_task`).

For a developer-oriented deep dive into tasks (naming, structure, examples), see `docs/TASKS.md`.