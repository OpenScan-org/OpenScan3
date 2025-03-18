from fastapi import APIRouter, HTTPException


from controllers.hardware.lights import LightControllerFactory

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
    try:
        controller = LightControllerFactory.get_controller_by_name(light_name)
        controller.turn_on()
        return {"status": "on", "name": light_name}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{light_name}/turn_off")
async def turn_off_light(light_name: str):
    try:
        controller = LightControllerFactory.get_controller_by_name(light_name)
        controller.turn_off()
        return {"status": "off", "name": light_name}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.post("/{light_name}/toggle")
async def toggle_light(light_name: str):
    try:
        controller = LightControllerFactory.get_controller_by_name(light_name)
        if controller.is_on():
            controller.turn_off()
            status = "off"
        else:
            controller.turn_on()
            status = "on"
        return {"status": status, "name": light_name}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))