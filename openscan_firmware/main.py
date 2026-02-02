import uvicorn
import logging
import json
from pathlib import Path
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from openscan_firmware.config.logger import setup_logging
from openscan_firmware.utils.settings import load_settings_json
from openscan_firmware import __version__

from openscan_firmware.routers import websocket as websocket_router
from openscan_firmware.routers.v0_6 import (
    cameras as cameras_v0_6,
    motors as motors_v0_6,
    lights as lights_v0_6,
    projects as projects_v0_6,
    gpio as gpio_v0_6,
    openscan as openscan_v0_6,
    device as device_v0_6,
    tasks as tasks_v0_6,
    develop as develop_v0_6,
    cloud as cloud_v0_6,
    focus_stacking as focus_stacking_v0_6,
)
# next routers
from openscan_firmware.routers.next import (
    projects as projects_next,

)
from openscan_firmware.controllers import device as device_controller

from openscan_firmware.controllers.services.tasks.task_manager import get_task_manager


logger = logging.getLogger(__name__)


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

    device_controller.initialize(device_controller.load_device_config())

    task_manager = get_task_manager()

    # Load firmware settings controlling task autodiscovery (with precedence and packaged defaults)
    autodiscovery_settings = load_settings_json("openscan_firmware.json", subdirectory="firmware") or {}

    if autodiscovery_settings.get("task_autodiscovery_enabled", True):
        namespaces = autodiscovery_settings.get(
            "task_autodiscovery_namespaces", ["openscan_firmware.controllers.services.tasks"]
        )
        include_subpackages = autodiscovery_settings.get(
            "task_autodiscovery_include_subpackages", True
        )
        ignore_modules = set(
            autodiscovery_settings.get("task_autodiscovery_ignore_modules", [])
        )
        safe_mode = autodiscovery_settings.get("task_autodiscovery_safe_mode", True)
        override_on_conflict = autodiscovery_settings.get(
            "task_autodiscovery_override_on_conflict", False
        )
        require_explicit_name = autodiscovery_settings.get(
            "task_require_explicit_name", True
        )
        raise_on_missing_name = autodiscovery_settings.get(
            "task_raise_on_missing_name", True
        )

        task_manager.autodiscover_tasks(
            namespaces=namespaces,
            include_subpackages=include_subpackages,
            ignore_modules=ignore_modules,
            safe_mode=safe_mode,
            override_on_conflict=override_on_conflict,
            require_explicit_name=require_explicit_name,
            raise_on_missing_name=raise_on_missing_name,
        )

        # Fail-fast on required core tasks
        if autodiscovery_settings.get("task_categories_enabled", True):
            required = set(autodiscovery_settings.get("task_required_core_names", []))
            missing = required - set(task_manager._task_registry.keys())
            if missing:
                raise RuntimeError(f"Missing required core tasks: {sorted(missing)}")
    else:
        # Fallback manual registration for development
        from openscan_firmware.controllers.services.tasks.core.scan_task import ScanTask as CoreScanTask
        from openscan_firmware.controllers.services.tasks.core.crop_task import CropTask as CoreCropTask

        task_manager.register_task("scan_task", CoreScanTask)
        task_manager.register_task("crop_task", CoreCropTask)

    # Now that tasks are registered, restore any persisted tasks
    task_manager.restore_tasks_from_persistence()

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

v0_6_ROUTERS = [
    cameras_v0_6.router,
    motors_v0_6.router,
    lights_v0_6.router,
    projects_v0_6.router,
    gpio_v0_6.router,
    openscan_v0_6.router,
    device_v0_6.router,
    tasks_v0_6.router,
    develop_v0_6.router,
    cloud_v0_6.router,
    websocket_router.router,
    focus_stacking_v0_6.router,
]

next_ROUTERS = [
    cameras_v0_6.router,
    motors_v0_6.router,
    lights_v0_6.router,
    projects_next.router,
    gpio_v0_6.router,
    openscan_v0_6.router,
    device_v0_6.router,
    tasks_v0_6.router,
    develop_v0_6.router,
    cloud_v0_6.router,
    websocket_router.router,
    focus_stacking_v0_6.router,
]


ROUTERS_BY_VERSION: dict[str, list] = {
    "0.6": v0_6_ROUTERS,
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
SUPPORTED_VERSIONS = [
    "0.6",
]
LATEST = SUPPORTED_VERSIONS[-1]

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
