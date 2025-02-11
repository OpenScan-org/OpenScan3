from fastapi import APIRouter, Body

from app.models.paths import PathMethod, PolarPoint3D
from controllers.hardware.cameras import cameras
from controllers.services import projects, scans
from app.services.paths import paths
from fastapi.responses import StreamingResponse
import asyncio

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
    await scans.move_to_point(point)


# https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events
@router.post("/scan")
async def start_scan(
    project_name: str = Body(embed=True),
    camera_id: int = Body(embed=True),
    method: PathMethod = Body(embed=True),
    points: int = Body(embed=True),
):
    project = projects.new_project(f"{project_name}")
    camera = cameras.get_camera(camera_id)
    path = paths.get_path(method, points)

    async def event_generator():
        async for step, total in scans.scan(project, camera, path):
            #yield b'event: status\ndata: {"step":"%s","total":"%s"}\n\n' % (bytes(str(step),'UTF-8'),bytes(str(total),'UTF-8'),)
            yield f'data: {{"step": {step}, "total": {total}}}\n\n'
            await asyncio.sleep(0.03)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream"
    )

@router.post("/reboot")
def reboot():
    scans.reboot()

@router.post("/shutdown")
def shutdown():
    scans.shutdown()