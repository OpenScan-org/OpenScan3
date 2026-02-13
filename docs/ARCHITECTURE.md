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

OpenScan3 uses a centralized background task system to coordinate long-running and/or hardware-critical operations such as scanning, cropping, and demo/example jobs. Implementation details, naming rules, and module structure live in [docs/TASKS.md](./TASKS.md).

### Startup Flow

During application startup (see `openscan_firmware/main.py` in the FastAPI lifespan handler):

1. Logging and device initialization are performed.
2. `TaskManager.initialize_core_tasks()` enforces the core task set. With `OPENSCAN_TASK_AUTODISCOVERY=1`, it runs discovery inside the default namespaces; otherwise it registers the built-in core implementations manually.
3. After initialization, `TaskManager.restore_tasks_from_persistence()` recovers previously persisted tasks.

### Task Discovery and Structure

Tasks live under `openscan_firmware/controllers/services/tasks/` (core + examples) and `openscan_firmware/tasks/community/`. See [docs/TASKS.md](./TASKS.md) for the full directory breakdown, opt-out flag, and ignore rules.

Concurrency limits, blocking/exclusive semantics, and configuration flags are documented in [docs/TASKS.md](./TASKS.md#concurrency--scheduling) and the env-table in [docs/FIRMWARE_ENV.md](./FIRMWARE_ENV.md#task-related-flags).

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