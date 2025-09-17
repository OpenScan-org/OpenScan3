import asyncio

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import StreamingResponse, Response
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel
from fastapi_versionizer import api_version

from app.config.camera import CameraSettings
from app.models.camera import Camera, CameraType
from app.controllers.hardware.cameras.camera import get_all_camera_controllers, get_camera_controller
#from app.controllers.services.scans import get_active_scan_manager
from app.controllers.hardware.motors import get_all_motor_controllers
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


@api_version(0,1)
@router.get("/", response_model=dict[str, CameraStatusResponse])
async def get_cameras():
    """Get all cameras with their current status"""
    return {
        name: controller.get_status()
        for name, controller in  get_all_camera_controllers().items()
    }


@api_version(0,1)
@router.get("/{camera_name}", response_model=CameraStatusResponse)
async def get_camera(camera_name: str):
    """Get a camera with its current status"""
    try:
        return get_camera_controller(camera_name).get_status()
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@api_version(0, 1)
@router.get("/{camera_name}/preview")
async def get_preview(camera_name: str):
    """Get a camera preview stream in lower resolution"""
    controller = get_camera_controller(camera_name)

    async def generate():
        while True:
            # Check if any motors are busy
            motor_busy = any(
                motor_controller.is_busy()
                for motor_controller in get_all_motor_controllers().values()
            )


            # Stop preview (wait) if motor or scan is busy, otherwise continue with 0.02s delay
            # if motor_busy or scan_busy:
             #   await asyncio.sleep(0.1)  # Small sleep to prevent busy waiting
             #   continue  # Skip frame generation and yield
            if not controller.is_busy():
                frame = controller.preview()
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
            await asyncio.sleep(0.02)

    return StreamingResponse(generate(), media_type="multipart/x-mixed-replace;boundary=frame")


@api_version(0,1)
@router.get("/{camera_name}/photo")
async def get_photo(camera_name: str):
    """Get a camera photo"""
    controller = get_camera_controller(camera_name)
    try:
        if not controller.is_busy():
            return Response(content=controller.photo().data.getvalue(), media_type="image/jpeg")
    except Exception as e:
        return Response(status_code=500, content=str(e))
    return Response(status_code=409, content="Camera is busy. If this is a bug, please restart the camera.")

@api_version(0,4)
@router.post("/{camera_name}/restart")
async def restart_camera(camera_name: str):
    """Restart a camera"""
    controller = get_camera_controller(camera_name)
    controller.restart_camera()
    return Response(status_code=200)

create_settings_endpoints(
    router=router,
    resource_name="camera_name",
    get_controller=get_camera_controller,
    settings_model=CameraSettings
)