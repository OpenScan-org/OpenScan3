import asyncio

from fastapi import APIRouter, Body, HTTPException, Query
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


def _get_motor_controller_or_404(motor_name: str):
    """Return the motor controller or raise a FastAPI 404."""

    try:
        return get_motor_controller(motor_name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


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
    return _get_motor_controller_or_404(motor_name).get_status()


@router.put("/{motor_name}/angle", response_model=MotorStatusResponse)
async def move_motor_to_angle(motor_name: str, degrees: float):
    """Move motor to absolute position

    Args:
        motor_name: The name of the motor to move
        degrees: Number of degrees to move

    Returns:
        MotorStatusResponse: A response object containing the status of the motor after the move
    """

    controller = _get_motor_controller_or_404(motor_name)
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
    controller = _get_motor_controller_or_404(motor_name)
    await controller.move_degrees(degrees)
    return controller.get_status()


@router.put("/{motor_name}/angle-override", response_model=MotorStatusResponse)
async def override_motor_angle(
    motor_name: str,
    angle: float = Query(
        90.0,
        description=(
            "Angle value that will overwrite the controller's internal model. Only change this "
            "after verifying the physical motor position because no positional feedback is available."
        ),
    ),
):
    """Override the internal motor angle model.

    This endpoint forces the controller's model to a specific angle without moving hardware. The
    default of 90° assumes the motor was manually aligned beforehand. Changing this value without
    confirming the actual motor position can desynchronize the model from reality and cause motion
    issues. The override is rejected while the controller reports a busy state to avoid writing an
    inconsistent angle during movements.

    Args:
        motor_name: Identifier of the motor whose model should be overwritten.
        angle: The new angle to store in the model (defaults to 90°).

    Returns:
        MotorStatusResponse: Updated status after overriding the model angle.
    """

    controller = _get_motor_controller_or_404(motor_name)
    if controller.is_busy():
        raise HTTPException(
            status_code=409,
            detail=(
                "Motor is currently moving. Stop the motion before overriding the internal angle "
                "model to avoid desynchronization."
            ),
        )

    controller.model.angle = angle
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
    controller = _get_motor_controller_or_404(motor_name)
    if controller.endstop and not controller.is_busy():
        # Trigger Endstop
        controller.model.angle = 0
        await controller.move_to_endstop()
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
