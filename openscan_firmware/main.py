import logging
import os

import uvicorn
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from openscan_firmware.config.logger import setup_logging
from openscan_firmware import __version__

from openscan_firmware.routers import websocket as websocket_router
from openscan_firmware.routers.v0_8 import (
    cameras as cameras_v0_8,
    motors as motors_v0_8,
    lights as lights_v0_8,
    projects as projects_v0_8,
    gpio as gpio_v0_8,
    openscan as openscan_v0_8,
    device as device_v0_8,
    tasks as tasks_v0_8,
    develop as develop_v0_8,
    cloud as cloud_v0_8,
    focus_stacking as focus_stacking_v0_8,
)
# v0.9 routers
from openscan_firmware.routers.v0_9 import (
    cameras as cameras_v0_9,
    motors as motors_v0_9,
    lights as lights_v0_9,
    firmware as firmware_v0_9,
    projects as projects_v0_9,
    gpio as gpio_v0_9,
    openscan as openscan_v0_9,
    device as device_v0_9,
    tasks as tasks_v0_9,
    develop as develop_v0_9,
    cloud as cloud_v0_9,
    focus_stacking as focus_stacking_v0_9,
)
# next routers
from openscan_firmware.routers.next import (
    cameras as cameras_next,
    motors as motors_next,
    lights as lights_next,
    firmware as firmware_next,
    projects as projects_next,
    gpio as gpio_next,
    openscan as openscan_next,
    device as device_next,
    tasks as tasks_next,
    develop as develop_next,
    cloud as cloud_next,
    focus_stacking as focus_stacking_next,
)
from openscan_firmware.controllers import device as device_controller

from openscan_firmware.controllers.services.tasks.task_manager import get_task_manager
from openscan_firmware.utils.firmware_state import handle_startup
from openscan_firmware.config.firmware import get_firmware_settings
from openscan_firmware.utils.wifi import is_network_ready_for_qr_scan


logger = logging.getLogger(__name__)


async def _maybe_start_qr_wifi_scan(task_manager) -> None:
    """Start the QR WiFi scan task only when no usable network is connected.

    This is called once during application startup.  The task runs indefinitely
    in the background until a WiFi QR code is found or the task is cancelled.
    """
    firmware_settings = get_firmware_settings()

    if not firmware_settings.qr_wifi_scan_enabled:
        logger.info("QR WiFi scan is disabled in firmware settings – skipping auto-start.")
        return

    if is_network_ready_for_qr_scan():
        logger.info("Network is already connected (WiFi/LAN) – skipping QR WiFi scan auto-start.")
        return

    # Find the first available camera to use for scanning
    from openscan_firmware.controllers.hardware.cameras.camera import get_all_camera_controllers
    cameras = get_all_camera_controllers()
    if not cameras:
        logger.warning("No camera controllers available – cannot auto-start QR WiFi scan.")
        return

    camera_name = next(iter(cameras))
    logger.info("No network connection detected. Starting QR WiFi scan task with camera '%s'.", camera_name)

    try:
        await task_manager.create_and_run_task("qr_scan_task", camera_name=camera_name)
    except Exception:
        logger.exception("Failed to auto-start QR WiFi scan task.")


REQUIRED_CORE_TASKS = [
    "scan_task",
    "focus_stacking_task",
    "cloud_upload_task",
    "cloud_download_task",
]


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Code to run on startup – configure logging with precedence and packaged defaults
    setup_logging(preferred_filename="advanced_logging.json", default_level=logging.DEBUG)

    logger.info(
        "OpenScan3 service starting (package version %s, API compatibility %s, latest v%s)",
        __version__,
        ", ".join(f"v{v}" for v in SUPPORTED_VERSIONS),
        LATEST,
    )

    handle_startup(logger)

    await device_controller.initialize(device_controller.load_device_config())

    task_manager = get_task_manager()

    autodiscovery_enabled = _env_flag("OPENSCAN_TASK_AUTODISCOVERY", False)
    override_on_conflict = _env_flag("OPENSCAN_TASK_OVERRIDE_ON_CONFLICT", False)

    task_manager.initialize_core_tasks(
        autodiscovery_enabled=autodiscovery_enabled,
        required_core_tasks=set(REQUIRED_CORE_TASKS),
        override_on_conflict=override_on_conflict,
    )

    # Now that tasks are registered, restore any persisted tasks
    task_manager.restore_tasks_from_persistence()

    # Auto-start QR WiFi scan if enabled and no network is connected
    await _maybe_start_qr_wifi_scan(task_manager)

    yield  # application runs here

    # Code to run on shutdown
    device_controller.cleanup_and_exit()
    logging.shutdown()


app = FastAPI(
    title="OpenScan3 API",
    description="REST interface controlling OpenScan hardware.",
    version=__version__,
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Create versioned sub-apps and mount them under /vX.Y and /latest
# Root app intentionally has no docs; each sub-app exposes its own docs.

v0_8_ROUTERS = [
    cameras_v0_8.router,
    motors_v0_8.router,
    lights_v0_8.router,
    projects_v0_8.router,
    gpio_v0_8.router,
    openscan_v0_8.router,
    device_v0_8.router,
    tasks_v0_8.router,
    develop_v0_8.router,
    cloud_v0_8.router,
    focus_stacking_v0_8.router,
    websocket_router.router,
]

next_ROUTERS = [
    cameras_next.router,
    motors_next.router,
    lights_next.router,
    firmware_next.router,
    projects_next.router,
    gpio_next.router,
    openscan_next.router,
    device_next.router,
    tasks_next.router,
    develop_next.router,
    cloud_next.router,
    websocket_router.router,
    focus_stacking_next.router,
]

v0_9_ROUTERS = [
    cameras_v0_9.router,
    motors_v0_9.router,
    lights_v0_9.router,
    firmware_v0_9.router,
    projects_v0_9.router,
    gpio_v0_9.router,
    openscan_v0_9.router,
    device_v0_9.router,
    tasks_v0_9.router,
    develop_v0_9.router,
    cloud_v0_9.router,
    websocket_router.router,
    focus_stacking_v0_9.router,
]


ROUTERS_BY_VERSION: dict[str, list] = {
    "0.8": v0_8_ROUTERS,
    "0.9": v0_9_ROUTERS,
    "next": next_ROUTERS,
}


def make_version_app(version: str) -> FastAPI:
    """Create a versioned FastAPI sub-application.

    Args:
        version: Semantic version string like "1.0".

    Returns:
        Configured FastAPI sub-app with routers and per-version docs.
    """
    sub = FastAPI(
        title=f"OpenScan3 API v{version}",
        version=version,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )


    # Include routers for this version (no extra prefixes; mount path provides version prefix)
    try:
        routers = ROUTERS_BY_VERSION[version]
    except KeyError as exc:
        raise ValueError(f"Unsupported API version requested: {version}") from exc

    for r in routers:
        sub.include_router(r)

    _use_route_names_as_operation_ids(sub)

    return sub


def _use_route_names_as_operation_ids(app: FastAPI) -> None:
    """Assign each APIRoute's operation_id to its route name.

    This helps with OpenAPI documentation generation and prevents names like 'deleteProjectProjectsProjectNameDelete'.

    Args:
        app: The FastAPI application whose routes should be updated.
    """
    seen: dict[str, int] = {}
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue

        base_name = route.name or getattr(route.endpoint, "__name__", None)
        if not base_name:
            continue

        count = seen.get(base_name, 0)
        seen[base_name] = count + 1

        operation_id = base_name if count == 0 else f"{base_name}_{count + 1}"
        route.operation_id = operation_id


# Supported API versions and latest alias
# Define the supported API versions and explicitly set the latest alias.
SUPPORTED_VERSIONS = [
    "0.8",
    "0.9",
]
LATEST = "0.9"

for v in SUPPORTED_VERSIONS:
    app.mount(f"/v{v}", make_version_app(v))

app.mount("/latest", make_version_app(LATEST))
app.mount("/next", make_version_app("next"))


@app.get("/versions")
def list_versions():
    """List available API versions and the current latest alias."""
    return {"versions": [f"{v}" for v in SUPPORTED_VERSIONS], "latest": f"v{LATEST}"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
