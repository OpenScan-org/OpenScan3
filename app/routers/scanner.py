from fastapi import APIRouter, Body

from app.models.paths import PathMethod, PolarPoint3D
from app.controllers.cameras import cameras
from app.controllers import scanner, projects
from app.services.paths import paths

router = APIRouter(
    prefix="",
    tags=["scanner"],
    responses={404: {"description": "Not found"}},
)


@router.get("/")
async def get_scanner():
    return {"status": "ok"}


@router.post("/move_to")
async def move_to_point(point: PolarPoint3D):
    scanner.move_to_point(point)


@router.post("/scan")
async def scan(
    project_name: str = Body(embed=True),
    camera_id: int = Body(embed=True),
    method: PathMethod = Body(embed=True),
    points: int = Body(embed=True),
):

    project = projects.new_project(f"{project_name}")
    camera = cameras.get_camera(camera_id)
    path = paths.get_path(method, points)
    scanner.scan(project, camera, path)

@router.post("/reboot")
def reboot():
    scanner.reboot()

@router.post("/shutdown")
def shutdown():
    scanner.shutdown()