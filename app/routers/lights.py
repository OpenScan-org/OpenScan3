from fastapi import APIRouter, HTTPException

from controllers.hardware import lights
from controllers.hardware.lights import LightControllerFactory
from app.models.light import Light

from app.config import config

router = APIRouter(
    prefix="/lights",
    tags=["lights"],
    responses={404: {"description": "Not found"}},
)


@router.get("/")
async def get_lights():
    """Get all motors with their current status"""
    return {
        name: controller.get_status()
        for name, controller in LightControllerFactory.get_all_controllers().items()
    }


@router.post("/{light_name}/turn_on")
async def turn_on_light(light_name: str):
    if light_name not in config.lights:
        raise HTTPException(status_code=404, detail=f"Light {light_name} not found")

    controller = LightControllerFactory.get_controller(
        config.lights[light_name]
    )
    controller.turn_on()


@router.post("/{light_name}/turn_off")
async def turn_on_light(light_name: str):
    if light_name not in config.lights:
        raise HTTPException(status_code=404, detail=f"Light {light_name} not found")

    controller = LightControllerFactory.get_controller(
        config.lights[light_name]
    )
    controller.turn_off()

@router.post("/{light_name}/toggle")
async def toggle_light(light_name: str):
    if light_name not in config.lights:
        raise HTTPException(status_code=404, detail=f"Light {light_name} not found")

    controller = LightControllerFactory.get_controller(
        config.lights[light_name]
    )
    if controller.is_on():
        controller.turn_off()
    else:
        controller.turn_on()


def get_light_controller(light: Light):
    return LightControllerFactory.get_controller(light)