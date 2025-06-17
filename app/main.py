import uvicorn
import logging
from fastapi import FastAPI, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi_versionizer.versionizer import Versionizer
from contextlib import asynccontextmanager

from app.config.logger import setup_logging_from_json_file

from routers import cameras, motors, projects, cloud, gpio, paths, openscan, lights, device, tasks
from app.controllers import device as device_controller


from app.controllers.services.tasks.task_manager import task_manager
from app.controllers.services.tasks.scan_task import ScanTask
# from app.controllers.services.tasks.example_tasks import HelloWorldTask, ExclusiveDemoTask

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Code to run on startup
    setup_logging_from_json_file(path_to_config="settings/advanced_logging.json", default_level=logging.DEBUG)

    device_controller.initialize(device_controller.load_device_config())

    # task_manager.register_task("hello_world", HelloWorldTask)
    # task_manager.register_task("exclusive_demo", ExclusiveDemoTask)
    task_manager.register_task("scan_task", ScanTask)

    yield # application runs here

    # Code to run on shutdown
    device_controller.cleanup_and_exit()


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(tasks.router)

app.include_router(cameras.router)
app.include_router(motors.router)
app.include_router(lights.router)
app.include_router(projects.router)
app.include_router(gpio.router)
app.include_router(openscan.router)

app.include_router(device.router)

app.include_router(cloud.router)
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