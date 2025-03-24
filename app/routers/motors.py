from fastapi import APIRouter, Body, HTTPException

from config.motor import MotorConfig
from app.controllers.hardware.motors import get_motor_controller, get_all_motor_controllers
from .settings_utils import create_settings_endpoints

router = APIRouter(
    prefix="/motors",
    tags=["motors"],
    responses={404: {"description": "Not found"}},
)

@router.get("/")
async def get_motors():
    """Get all motors with their current status"""
    return {
        name: controller.get_status()
        for name, controller in get_all_motor_controllers().items()
    }


@router.post("/{motor_name}/move_to_degree")
async def move_motor(motor_name: str, degrees: float):
    controller = get_motor_controller(motor_name)
    await controller.move_to(degrees)
    return controller.get_status()


@router.post("/{motor_name}/move_degrees")
async def move_motor(motor_name: str, degrees: float = Body(embed=True)):
    controller = get_motor_controller(motor_name)
    await controller.move_degrees(degrees)

create_settings_endpoints(
    router=router,
    resource_name="motor_name",
    get_controller=get_motor_controller,
    settings_model=MotorConfig
)