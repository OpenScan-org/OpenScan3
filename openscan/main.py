import uvicorn
import logging
import json
from pathlib import Path
from fastapi import FastAPI, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi_versionizer.versionizer import Versionizer
from contextlib import asynccontextmanager

from openscan.config.logger import setup_logging, load_settings_json

from openscan.routers import cameras, motors, projects, gpio, paths, openscan, lights, device, tasks, develop
from openscan.controllers import device as device_controller


from openscan.controllers.services.tasks.task_manager import get_task_manager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Code to run on startup – configure logging with precedence and packaged defaults
    setup_logging(preferred_filename="advanced_logging.json", default_level=logging.DEBUG)

    device_controller.initialize(device_controller.load_device_config())

    task_manager = get_task_manager()

    # Load firmware settings controlling task autodiscovery (with precedence and packaged defaults)
    autodiscovery_settings = load_settings_json("openscan_firmware.json") or {}

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


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(tasks.router)
app.include_router(develop.router)

app.include_router(cameras.router)
app.include_router(motors.router)
app.include_router(lights.router)
app.include_router(projects.router)
app.include_router(gpio.router)
app.include_router(openscan.router)

app.include_router(device.router)

#app.include_router(cloud.router)
app.include_router(paths.router)


versions = Versionizer(
    app=app,
    prefix_format='/v{major}.{minor}',
    semantic_version_format='{major}.{minor}',
    latest_prefix='/latest', # makes the latest version of endpoints available at /latest
    include_versions_route=True, # adds a GET /versions route
    include_version_docs=True, # adds GET /{version}/docs and GET /{version}/redoc
    sort_routes=False
).versionize()


# add static files mounts here, because they won't work before versionizer is initialized
# e.g.: app.mount("/static", app.static_files, name="static")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)