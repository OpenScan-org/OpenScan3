from fastapi import APIRouter, HTTPException

from app.controllers.hardware.lights import get_light_controller, get_all_light_controllers

router = APIRouter(
    prefix="/lights",
    tags=["lights"],
    responses={404: {"description": "Not found"}},
)


@router.get("/")
async def get_lights():
    """Get all lights with their current status"""
    return {
        name: controller.get_status()
        for name, controller in get_all_light_controllers().items()
    }


@router.post("/{light_name}/turn_on")
async def turn_on_light(light_name: str):
    try:
        controller = get_light_controller(light_name)
        controller.turn_on()
        return controller.get_status()
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{light_name}/turn_off")
async def turn_off_light(light_name: str):
    try:
        controller = get_light_controller(light_name)
        controller.turn_off()
        return controller.get_status()
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.post("/{light_name}/toggle")
async def toggle_light(light_name: str):
    try:
        controller = get_light_controller(light_name)
        if controller.is_on():
            controller.turn_off()
        else:
            controller.turn_on()
        return controller.get_status()
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))