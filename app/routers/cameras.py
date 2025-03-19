import asyncio

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import StreamingResponse, Response
from fastapi.encoders import jsonable_encoder

from app.config.camera import CameraSettings
from app.controllers.hardware.cameras.camera import get_all_camera_controllers, get_camera_controller_by_id

router = APIRouter(
    prefix="/cameras",
    tags=["cameras"],
    responses={404: {"description": "Not found"}},
)


@router.get("/")
async def get_cameras():
    return {
        name: controller.get_all_settings()
        for name, controller in  get_all_camera_controllers().items()
    }

@router.get("/{camera_id}")
async def get_camera(camera_id: int):
    controller = get_camera_controller_by_id(camera_id)
    return controller.get_all_settings()

@router.get("/{camera_id}/preview")
async def get_preview(camera_id: int):
    controller = get_camera_controller_by_id(camera_id)


    async def generate():
        while True:
            frame = controller.preview()
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
            await asyncio.sleep(0.02)

    return StreamingResponse(generate(), media_type="multipart/x-mixed-replace;boundary=frame")

@router.get("/{camera_id}/photo")
async def get_photo(camera_id: int):
    controller = get_camera_controller_by_id(camera_id)
    return Response(content=controller.photo(), media_type="image/jpeg")


@router.get("/{camera_id}/settings")
async def get_camera_settings(camera_id: int):
    controller = get_camera_controller_by_id(camera_id)
    return jsonable_encoder(controller.get_all_settings())


@router.put("/{camera_id}/settings")
async def set_camera_settings(camera_id: int, settings = Body(...)):
    controller = get_camera_controller_by_id(camera_id)
    try:
        new_settings = CameraSettings(**settings)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Error in provided settings.\n{e}")
    if controller.replace_settings(new_settings):
        return {"message": "Settings updated"}
    else:
        raise HTTPException(status_code=422, detail="Error in provided settings.")



@router.patch("/{camera_id}/settings")
async def update_camera_setting(camera_id: int, setting: str, value):
    controller = get_camera_controller_by_id(camera_id)
    try:
        # convert str value to expected settings type
        value = controller.settings_manager.convert_value(setting, value)
        # update setting
        if controller.set_setting(setting, value):
            return {"message": f"Setting {setting} set to {value}"}
    except:
        raise HTTPException(status_code=422, detail="Error in provided settings.")