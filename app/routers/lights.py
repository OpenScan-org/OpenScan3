from fastapi import APIRouter, HTTPException
from fastapi_versionizer import api_version
from pydantic import BaseModel

from app.controllers.hardware.lights import get_light_controller, get_all_light_controllers
from config.light import LightConfig
from .settings_utils import create_settings_endpoints

router = APIRouter(
    prefix="/lights",
    tags=["lights"],
    responses={404: {"description": "Not found"}},
)

class LightStatusResponse(BaseModel):
    name: str
    is_on: bool
    settings: LightConfig


@api_version(0,1)
@router.get("/", response_model=dict[str, LightStatusResponse])
async def get_lights():
    """Get all lights with their current status"""
    return {
        name: controller.get_status()
        for name, controller in get_all_light_controllers().items()
    }


@api_version(0,1)
@router.get("/{light_name}", response_model=LightStatusResponse)
async def get_light(light_name: str):
    """Get light status"""
    try:
        return get_light_controller(light_name).get_status()
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@api_version(0,1)
@router.patch("/{light_name}/turn_on", response_model=LightStatusResponse)
async def turn_on_light(light_name: str):
    """Turn on light"""
    try:
        controller = get_light_controller(light_name)
        controller.turn_on()
        return controller.get_status()
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@api_version(0,1)
@router.patch("/{light_name}/turn_off", response_model=LightStatusResponse)
async def turn_off_light(light_name: str):
    """Turn of light"""
    try:
        controller = get_light_controller(light_name)
        controller.turn_off()
        return controller.get_status()
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@api_version(0,1)
@router.patch("/{light_name}/toggle", response_model=LightStatusResponse)
async def toggle_light(light_name: str):
    """Toggle light on or off"""
    try:
        controller = get_light_controller(light_name)
        if controller.is_on():
            controller.turn_off()
        else:
            controller.turn_on()
        return controller.get_status()
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


create_settings_endpoints(
    router=router,
    resource_name="light_name",
    get_controller=get_light_controller,
    settings_model=LightConfig
)