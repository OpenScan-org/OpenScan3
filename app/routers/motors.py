import asyncio

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel
from typing import Optional
from fastapi_versionizer import api_version

from app.config.motor import MotorConfig
from app.controllers.hardware.motors import get_motor_controller, get_all_motor_controllers
from app.models.paths import PolarPoint3D
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


@api_version(0,1)
@router.get("/", response_model=dict[str, MotorStatusResponse])
async def get_motors():
    """Get all motors with their current status"""
    return {
        name: controller.get_status()
        for name, controller in get_all_motor_controllers().items()
    }


@api_version(0,1)
@router.get("/{motor_name}", response_model=MotorStatusResponse)
async def get_motor(motor_name: str):
    """Get motor status"""
    try:
        return get_motor_controller(motor_name).get_status()
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@api_version(0,1)
@router.put("/{motor_name}/angle", response_model=MotorStatusResponse)
async def move_motor_to_angle(motor_name: str, degrees: float):
    """Move motor to absolute position"""
    controller = get_motor_controller(motor_name)
    await controller.move_to(degrees)
    return controller.get_status()


@api_version(0,1)
@router.patch("/{motor_name}/angle", response_model=MotorStatusResponse)
async def move_motor_by_degree(motor_name: str, degrees: float = Body(embed=True)):
    """Move motor by degrees"""
    controller = get_motor_controller(motor_name)
    await controller.move_degrees(degrees)
    return controller.get_status()


@api_version(0,2)
@router.put("/{motor_name}/endstop-calibration", response_model=MotorStatusResponse)
async def move_motor_to_home_position(motor_name: str):
    """Move motor to home position"""
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