from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel

from config.motor import MotorConfig
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
    target_angle: float
    settings: MotorConfig



@router.get("/", response_model=dict[str, MotorStatusResponse])
async def get_motors():
    """Get all motors with their current status"""
    return {
        name: controller.get_status()
        for name, controller in get_all_motor_controllers().items()
    }

@router.get("/{motor_name}", response_model=MotorStatusResponse)
async def get_motor(motor_name: str):
    """Get motor status"""
    try:
        return get_motor_controller(motor_name).get_status()
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.put("/{motor_name}/angle", response_model=MotorStatusResponse)
async def move_motor_to_angle(motor_name: str, degrees: float):
    """Move motor to absolute position"""
    controller = get_motor_controller(motor_name)
    await controller.move_to(degrees)
    return controller.get_status()


@router.patch("/{motor_name}/angle", response_model=MotorStatusResponse)
async def move_motor_by_degree(motor_name: str, degrees: float = Body(embed=True)):
    """Move motor by degrees"""
    controller = get_motor_controller(motor_name)
    await controller.move_degrees(degrees)
    return controller.get_status()


create_settings_endpoints(
    router=router,
    resource_name="motor_name",
    get_controller=get_motor_controller,
    settings_model=MotorConfig
)