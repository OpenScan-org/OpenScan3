import uvicorn
import logging
import json
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from openscan.config.logger import setup_logging
from openscan.utils.settings import load_settings_json

from openscan.routers import cameras, motors, projects, gpio, paths, openscan, lights, device, tasks, develop, cloud
from openscan.controllers import device as device_controller


from openscan.controllers.services.tasks.task_manager import get_task_manager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Code to run on startup – configure logging with precedence and packaged defaults
    setup_logging(preferred_filename="advanced_logging.json", default_level=logging.DEBUG)

    device_controller.initialize(device_controller.load_device_config())

    task_manager = get_task_manager()

    # Load firmware settings controlling task autodiscovery (with precedence and packaged defaults)
    autodiscovery_settings = load_settings_json("openscan_firmware.json", subdirectory="firmware") or {}

    if autodiscovery_settings.get("task_autodiscovery_enabled", True):
        namespaces = autodiscovery_settings.get(
            "task_autodiscovery_namespaces", ["openscan.controllers.services.tasks"]
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
        from openscan.controllers.services.tasks.core.scan_task import ScanTask as CoreScanTask
        from openscan.controllers.services.tasks.core.crop_task import CropTask as CoreCropTask

        task_manager.register_task("scan_task", CoreScanTask)
        task_manager.register_task("crop_task", CoreCropTask)

    # Now that tasks are registered, restore any persisted tasks
    task_manager.restore_tasks_from_persistence()

    yield # application runs here

    # Code to run on shutdown
    device_controller.cleanup_and_exit()
    logging.shutdown()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Create versioned sub-apps and mount them under /vX.Y and /latest
# Root app intentionally has no docs; each sub-app exposes its own docs.

# Base routers shared across versions by default
BASE_ROUTERS = [
    cameras.router,
    motors.router,
    lights.router,
    projects.router,
    gpio.router,
    openscan.router,
    device.router,
    tasks.router,
    develop.router,
    paths.router,
    cloud.router,
]

# Router mapping per API version. Extend per version to diverge.
# Example: "0.2": BASE_ROUTERS + [new_feature.router]
ROUTERS_BY_VERSION: dict[str, list] = {
    "0.2": BASE_ROUTERS,
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

    # Apply middleware for mounted sub-apps
    sub.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Include routers for this version (no extra prefixes; mount path provides version prefix)
    for r in ROUTERS_BY_VERSION.get(version, BASE_ROUTERS):
        sub.include_router(r)

    return sub


# Supported API versions and latest alias
SUPPORTED_VERSIONS = [
    "0.1",
    "0.2",
]
LATEST = SUPPORTED_VERSIONS[-1]

for v in SUPPORTED_VERSIONS:
    app.mount(f"/v{v}", make_version_app(v))

app.mount("/latest", make_version_app(LATEST))


@app.get("/versions")
def list_versions():
    """List available API versions and the current latest alias."""
    return {"versions": [f"v{v}" for v in SUPPORTED_VERSIONS], "latest": f"v{LATEST}"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)