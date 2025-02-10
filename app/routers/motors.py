from fastapi import APIRouter, Body, HTTPException

from controllers.hardware import motors
from app.config import config
from controllers.hardware.motors import MotorControllerFactory

from app.models.motor import Motor

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
    if motor_name not in config.motors:
        raise HTTPException(status_code=404, detail=f"Motor {motor_name} not found")

    controller = MotorControllerFactory.get_controller(config.motors[motor_name])
    await controller.move_to(degrees)
    return controller.get_status()


@router.post("/{motor_name}/move")
async def move_motor(motor_name: str, degrees: float = Body(embed=True)):
    motor = config.motors[motor_name]
    controller = get_motor_controller(motor)
    await controller.move_degrees(degrees)

    #motors.move_motor_degrees(motor, degrees)

def get_motor_controller(motor: Motor):
    return MotorControllerFactory.get_controller(motor)