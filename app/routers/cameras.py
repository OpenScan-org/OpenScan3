import asyncio

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import StreamingResponse, Response
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel

from app.config.camera import CameraSettings
from app.models.camera import Camera, CameraType
from app.controllers.hardware.cameras.camera import get_all_camera_controllers, get_camera_controller
from .settings_utils import create_settings_endpoints

router = APIRouter(
    prefix="/cameras",
    tags=["cameras"],
    responses={404: {"description": "Not found"}},
)

class CameraStatusResponse(BaseModel):
    name: str
    type: CameraType
    busy: bool
    settings: CameraSettings

@router.get("/", response_model=dict[str, CameraStatusResponse])
async def get_cameras():
    """Get all cameras with their current status"""
    return {
        name: controller.get_status()
        for name, controller in  get_all_camera_controllers().items()
    }

@router.get("/{camera_name}", response_model=CameraStatusResponse)
async def get_camera(camera_name: str):
    """Get a camera with its current status"""
    try:
        return get_camera_controller(camera_name).get_status()
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.get("/{camera_name}/preview")
async def get_preview(camera_name: str):
    """Get a camera preview stream in lower resolution"""
    controller = controller = get_camera_controller(camera_name)


    async def generate():
        while True:
            frame = controller.preview()
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
            await asyncio.sleep(0.02)

    return StreamingResponse(generate(), media_type="multipart/x-mixed-replace;boundary=frame")

@router.get("/{camera_name}/photo")
async def get_photo(camera_name: str):
    """Get a camera photo"""
    controller = get_camera_controller(camera_name)
    return Response(content=controller.photo(), media_type="image/jpeg")


create_settings_endpoints(
    router=router,
    resource_name="camera_name",
    get_controller=get_camera_controller,
    settings_model=CameraSettings
)