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
   - Routers in `openscan_firmware/routers` handle incoming API requests.

2. **Controllers**
   - Controllers in `openscan_firmware/controllers` implement the business logic.
   - Settings are managed at runtime within `openscan_firmware/controllers/settings.py`
   - Detection and initialization of hardware will be managed by the Device Controller within `openscan_firmware/controllers/hardware/device.py`, which is also capable of managing configuration profiles
   2.1. **Hardware Controllers**
      - Located in `openscan_firmware/controllers/hardware` control the hardware components of the scanner.
      - Abstracts the hardware details from the application logic.
      - Supports multiple hardware configurations.
      - Hardware is divided into three categories: stateful hardware (like motors and cameras), switchable hardware (like lights), and event hardware (like buttons and simple sensors).
         - HardwareControllers inherit from the according HardwareInterface class in `openscan_firmware/controllers/hardware/interfaces.py`.
         - HardwareControllers instantiate the settings manager in `openscan_firmware/controllers/settings.py` to manage and update settings.

   2.2. **Service Controllers**
      - Controllers in `openscan_firmware/controllers/services` handle the business logic of the scanner.
      - Examples: Managing scan projects and scan procedures.
   
3. **Configuration Management**
   - Located in `openscan_firmware/config`.
   - Manages settings for different components.

4. **Models and Data Structures**
   - Defined in `openscan_firmware/models`.
   - Represents the data entities and their relationships.



## Interactions

- The FastAPI application receives requests from clients and routes them to the appropriate controllers via routers.
- Controllers interact with the hardware abstraction layer to perform operations on the scanner.

This architecture is designed to be modular and extensible, allowing for easy integration of new hardware components and features.

## Background Task System

OpenScan3 uses a centralized background task system to coordinate long-running and/or hardware-critical operations such as scanning, cropping, and demo/example jobs.

- The task system is implemented under `openscan_firmware/controllers/services/tasks/`.
- The central orchestrator is `TaskManager` (`openscan_firmware/controllers/services/tasks/task_manager.py`).
- Tasks are classes that inherit from `BaseTask` and declare an explicit, snake_case `task_name` ending with `_task` (e.g., `scan_task`).
- Tasks are auto-discovered at startup when `OPENSCAN_TASK_AUTODISCOVERY=1` is set; otherwise the built-in core tasks are registered manually.

### Startup Flow

During application startup (see `openscan_firmware/main.py` in the FastAPI lifespan handler):

1. Logging and device initialization are performed.
2. If `OPENSCAN_TASK_AUTODISCOVERY=1`, `TaskManager.autodiscover_tasks()` scans the built-in namespaces with strict naming/ignore policies; otherwise the core tasks are registered manually.
4. After discovery, the firmware always validates that the fixed core tasks (`scan_task`, `focus_stacking_task`, `cloud_upload_task`, `cloud_download_task`) are registered. Missing tasks raise a `RuntimeError` and abort startup.
5. After successful registration, `TaskManager.restore_tasks_from_persistence()` is called to recover previously persisted tasks.

### Task Discovery and Structure

Tasks are organized in the following locations:

- Core (production) tasks: `openscan_firmware/controllers/services/tasks/core/`
  - e.g., `core/scan_task.py` (exclusive, async generator), `core/cloud_task.py` (cloud upload/download orchestration)
- Example/demo tasks: `openscan_firmware/controllers/services/tasks/examples/`
  - e.g., `examples/demo_examples.py`, `examples/crop_task.py` (blocking contour analysis)
- Community tasks: `openscan_firmware/tasks/community/`

Modules can opt-out from autodiscovery by setting a module-level flag `__openscan_autodiscover__ = False`. A global ignore list can also be configured in the settings file.

### Concurrency & Scheduling

The `TaskManager` enforces the following semantics:

- Non-exclusive tasks can run in parallel up to a fixed limit (`MAX_CONCURRENT_NON_EXCLUSIVE_TASKS`).
- Exclusive tasks require sole access and will prevent other tasks from starting; they are queued if necessary.
- Blocking tasks (`is_blocking = True`) run in a thread pool and do not count against the async concurrency limit. Exclusive semantics still apply.
- Scheduling decisions are encapsulated in `TaskManager._can_run_task` and the internal queueing logic.


### Configuration

Autodiscovery is disabled in production images. Set `OPENSCAN_TASK_AUTODISCOVERY=1` (and optionally
`OPENSCAN_TASK_OVERRIDE_ON_CONFLICT=1`) to enable it for developer builds. The `TaskManager` always
uses the same internal defaults: it scans `openscan_firmware.controllers.services.tasks` and
`openscan_firmware.tasks.community`, recurses into subpackages, and ignores helper modules such as
`base_task`, `task_manager`, and `examples*`. Core tasks are fixed in code; if `scan_task`,
`focus_stacking_task`, `cloud_upload_task`, or `cloud_download_task` are missing after discovery,
startup aborts.

For a developer-oriented deep dive into tasks (naming, structure, examples), see `docs/TASKS.md`.

### Service Layer for Scans

The deprecated `ScanManager` was replaced by a stateless service layer in `openscan_firmware/controllers/services/scans.py`:

- `start_scan(project_manager, scan, camera_controller, start_from_step=0)` creates and runs a `scan_task` via the `TaskManager`.
- `pause_scan(scan)`, `resume_scan(scan)`, `cancel_scan(scan)` delegate task lifecycle operations to the `TaskManager`.

Routers (`openscan_firmware/routers/`) call this service layer (e.g., in `projects.py`), avoiding direct import-time coupling to task modules.

## API Versioning

OpenScan3 exposes versioned APIs using mounted FastAPI sub-apps.

- Versions are mounted under `/vX.Y` (e.g., `/v1.0`).
- The latest stable API is additionally mounted under `/latest`.
- Each version has its own OpenAPI and docs endpoints:
  - `/vX.Y/openapi.json`, `/vX.Y/docs`, `/vX.Y/redoc`
  - `/latest/openapi.json`, `/latest/docs`, `/latest/redoc`
- The root app provides a simple discovery endpoint at `/versions` returning the list of available versions and the current latest alias.

Implementation details (see `openscan_firmware/main.py`):

- The root `FastAPI` app defines the global Lifecycle (logging, hardware init, task autodiscovery) so initialization happens only once.
- Routers are grouped by API track under `openscan_firmware/routers/<version>/` (e.g., `v0_6`, `next`). `make_version_app(version)` looks up the router list from `ROUTERS_BY_VERSION` and mounts it without extra prefixes; the mount path provides the version prefix.
- CORS (and other middlewares if needed) are added per sub-app so they apply within the mounted context.
- A `/next` preview app can be mounted alongside stable releases for early testing without affecting `/latest`.

Client guidance:

- Prefer `/latest/...` to always track the current stable API.
- Pin to `/vX.Y/...` if you need strict compatibility.