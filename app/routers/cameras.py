import asyncio
from fastapi import APIRouter
from fastapi.responses import StreamingResponse, Response
from fastapi.encoders import jsonable_encoder

from app.controllers.cameras import cameras

router = APIRouter(
    prefix="/cameras",
    tags=["cameras"],
    responses={404: {"description": "Not found"}},
)


@router.get("/")
async def get_cameras():
    return jsonable_encoder(cameras.get_cameras())


@router.get("/{camera_id}")
async def get_camera(camera_id: int):
    return jsonable_encoder(cameras.get_camera(camera_id))


@router.get("/{camera_id}/preview")
async def get_preview(camera_id: int):
    camera = cameras.get_camera(camera_id)
    controller = cameras.get_camera_controller(camera)

    async def generate():
        while True:
            yield (b'--frame\r\n' b'Content-Type: image/jpeg\r\n\r\n' + controller.preview(camera).read() + b'\r\n')
            await asyncio.sleep(0.03)

    return StreamingResponse(generate(), media_type="multipart/x-mixed-replace;boundary=frame")
    # return Response(controller.preview(camera).read(), media_type="image/png")


@router.get("/{camera_id}/photo")
async def get_photo(camera_id: int):
    camera = cameras.get_camera(camera_id)
    controller = cameras.get_camera_controller(camera)
    return Response(controller.photo(camera).read(), media_type="image/png")
