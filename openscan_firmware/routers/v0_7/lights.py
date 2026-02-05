from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from openscan_firmware.controllers.hardware.lights import get_light_controller, get_all_light_controllers
from openscan_firmware.config.light import LightConfig
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


@router.get("/", response_model=dict[str, LightStatusResponse])
async def get_lights():
    """Get all lights with their current status

    Returns:
        dict[str, LightStatusResponse]: A dictionary of light name to a light status object
    """
    return {
        name: controller.get_status()
        for name, controller in get_all_light_controllers().items()
    }


@router.get("/{light_name}", response_model=LightStatusResponse)
async def get_light(light_name: str):
    """Get light status

    Args:
        light_name: The name of the light to get the status of

    Returns:
        LightStatusResponse: A response object containing the status of the light
    """
    try:
        return get_light_controller(light_name).get_status()
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.patch("/{light_name}/turn_on", response_model=LightStatusResponse)
async def turn_on_light(light_name: str):
    """Turn on light

    Args:
        light_name: The name of the light to turn on

    Returns:
        LightStatusResponse: A response object containing the status of the light after the turn on operation
    """
    try:
        controller = get_light_controller(light_name)
        controller.turn_on()
        return controller.get_status()
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.patch("/{light_name}/turn_off", response_model=LightStatusResponse)
async def turn_off_light(light_name: str):
    """Turn of light

    Args:
        light_name: The name of the light to turn off

    Returns:
        LightStatusResponse: A response object containing the status of the light after the turn off operation
    """
    try:
        controller = get_light_controller(light_name)
        controller.turn_off()
        return controller.get_status()
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.patch("/{light_name}/toggle", response_model=LightStatusResponse)
async def toggle_light(light_name: str):
    """Toggle light on or off

    Args:
        light_name: The name of the light to toggle

    Returns:
        LightStatusResponse: A response object containing the status of the light after the toggle operation
    """
    try:
        controller = get_light_controller(light_name)
        if controller.is_on:
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