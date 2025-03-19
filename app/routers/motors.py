from fastapi import APIRouter, Body, HTTPException

from app.controllers.hardware.motors import get_motor_controller, get_all_motor_controllers

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


@router.post("/{motor_name}/move_to")
async def move_motor(motor_name: str, degrees: float):
    controller = get_motor_controller(motor_name)
    await controller.move_to(degrees)
    return controller.get_status()


@router.post("/{motor_name}/move")
async def move_motor(motor_name: str, degrees: float = Body(embed=True)):
    controller = get_motor_controller(motor_name)
    await controller.move_degrees(degrees)
