import time
from fastapi import APIRouter

from app.controllers import scanner
from app.controllers.cameras import cameras
from app.controllers import projects
from app.services.paths import paths
from app.models.paths import PathMethod

router = APIRouter(
    prefix="/scanner",
    tags=["scanner"],
    responses={404: {"description": "Not found"}},
)


@router.get("/")
async def get_scanner():
    return {}


@router.post("/move_to")
async def move_to_point(point: paths.PolarPoint3D):
    scanner.move_to_point(point)


@router.post("/scan")
async def scan(project_name: str, camera_id: int, method: PathMethod, points: int):
    project = projects.new_project(f"{project_name}_{int(time.time())}")
    camera = cameras.get_camera(camera_id)
    path = paths.get_path(method, points)
    scanner.scan(project, camera, path)
