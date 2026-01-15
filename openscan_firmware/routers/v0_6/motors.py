import asyncio

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel
from typing import Optional

from openscan_firmware.config.motor import MotorConfig
from openscan_firmware.controllers.hardware.motors import get_motor_controller, get_all_motor_controllers
from openscan_firmware.models.paths import PolarPoint3D
from .settings_utils import create_settings_endpoints

router = APIRouter(
    prefix="/motors",
    tags=["motors"],
    responses={404: {"description": "Not found"}},
)

class MotorStatusResponse(BaseModel):
    name: str
    angle: float
    busy: bool
    target_angle: Optional[float]
    settings: MotorConfig
    endstop: Optional[dict]


@router.get("/", response_model=dict[str, MotorStatusResponse])
async def get_motors():
    """Get all motors with their current status

    Returns:
        dict[str, MotorStatusResponse]: A dictionary of motor name to a motor status object
    """
    return {
        name: controller.get_status()
        for name, controller in get_all_motor_controllers().items()
    }


@router.get("/{motor_name}", response_model=MotorStatusResponse)
async def get_motor(motor_name: str):
    """Get motor status

    Args:
        motor_name: The name of the motor to get the status of

    Returns:
        MotorStatusResponse: A response object containing the status of the motor
    """
    try:
        return get_motor_controller(motor_name).get_status()
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.put("/{motor_name}/angle", response_model=MotorStatusResponse)
async def move_motor_to_angle(motor_name: str, degrees: float):
    """Move motor to absolute position

    Args:
        motor_name: The name of the motor to move
        degrees: Number of degrees to move

    Returns:
        MotorStatusResponse: A response object containing the status of the motor after the move
    """

    controller = get_motor_controller(motor_name)
    await controller.move_to(degrees)
    return controller.get_status()


@router.patch("/{motor_name}/angle", response_model=MotorStatusResponse)
async def move_motor_by_degree(motor_name: str, degrees: float = Body(embed=True)):
    """Move motor by degrees

    Args:
        motor_name: The name of the motor to move
        degrees: Number of degrees to move

    Returns:
        MotorStatusResponse: A response object containing the status of the motor after the move
    """
    controller = get_motor_controller(motor_name)
    await controller.move_degrees(degrees)
    return controller.get_status()


@router.put("/{motor_name}/endstop-calibration", response_model=MotorStatusResponse)
async def move_motor_to_home_position(motor_name: str):
    """Move motor to home position

    This endpoint moves the motor to the home position using the endstop calibration.

    Args:
        motor_name: The name of the motor to move to the home position

    Returns:
        MotorStatusResponse: A response object containing the status of the motor after the move
    """
    controller = get_motor_controller(motor_name)
    if controller.endstop and not controller.is_busy():
        # Trigger Endstop
        controller.model.angle = 0
        await controller.move_degrees(140)
        # Wait for Endstop and move motor to home position
        await asyncio.sleep(3)
        await controller.move_to(90)
        return controller.get_status()
    else:
        raise HTTPException(status_code=422, detail="No endstop configured or motor is busy!")


create_settings_endpoints(
    router=router,
    resource_name="motor_name",
    get_controller=get_motor_controller,
    settings_model=MotorConfig
)