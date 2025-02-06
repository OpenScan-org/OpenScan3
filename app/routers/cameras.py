import asyncio
from xml.dom import ValidationErr

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import StreamingResponse, Response
from fastapi.encoders import jsonable_encoder

from app.controllers.cameras import cameras
from app.config.camera import CameraSettings



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
            frame = controller.preview()
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
            await asyncio.sleep(0.02)

    return StreamingResponse(generate(), media_type="multipart/x-mixed-replace;boundary=frame")

@router.get("/{camera_id}/photo")
async def get_photo(camera_id: int):
    camera = cameras.get_camera(camera_id)
    controller = cameras.get_camera_controller(camera)
    #return Response(controller.photo().read(), media_type="image/jpeg")
    return Response(content=controller.photo(), media_type="image/jpeg")


@router.get("/{camera_id}/settings")
async def get_camera_settings(camera_id: int):
    camera = cameras.get_camera(camera_id)
    controller = cameras.get_camera_controller(camera)
    return jsonable_encoder(controller.get_all_settings())


@router.put("/{camera_id}/settings")
async def set_camera_settings(camera_id: int, settings = Body(...)):
    camera = cameras.get_camera(camera_id)
    controller = cameras.get_camera_controller(camera)
    try:
        new_settings = CameraSettings(**settings)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Error in provided settings.\n{e}")
    if controller.set_settings(new_settings):
        return {"message": "Settings updated"}
    else:
        raise HTTPException(status_code=422, detail="Error in provided settings.")



@router.patch("/{camera_id}/settings")
async def update_camera_setting(camera_id: int, setting: str, value):
    camera = cameras.get_camera(camera_id)
    controller = cameras.get_camera_controller(camera)
    if controller.update_setting(setting, value):
        return {"message": f"Setting {setting} set to {value}"}
    else:
        raise HTTPException(status_code=422, detail="Error in provided settings.")