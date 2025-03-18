from fastapi import APIRouter, Body, HTTPException

from app.controllers.hardware.motors import MotorControllerFactory

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
        for name, controller in MotorControllerFactory.get_all_controllers().items()
    }


#@router.get("/{motor_type}")
#async def get_motor(motor_type: MotorType):
#    return motors.get_motor(motor_type)


@router.post("/{motor_name}/move_to")
async def move_motor(motor_name: str, degrees: float):
    controller = MotorControllerFactory.get_controller_by_name(motor_name)
    await controller.move_to(degrees)
    return controller.get_status()


@router.post("/{motor_name}/move")
async def move_motor(motor_name: str, degrees: float = Body(embed=True)):
    controller = MotorControllerFactory.get_controller_by_name(motor_name)
    await controller.move_degrees(degrees)
