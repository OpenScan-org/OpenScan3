from fastapi import APIRouter
from fastapi_versionizer import api_version

from app.controllers.hardware import gpio

router = APIRouter(
    prefix="/gpio",
    tags=["gpio"],
    responses={404: {"description": "Not found"}},
)


@api_version(0,1)
@router.get("/")
async def get_pins():
    """Get all initialized GPIO pins"""
    return gpio.get_initialized_pins()


@api_version(0,1)
@router.get("/{pin_id}", response_model=bool)
async def get_pin(pin_id: int):
    """Get output value of a specific GPIO pin"""
    return gpio.get_output_pin(pin_id)


@api_version(0,1)
@router.patch("/{pin_id}")
async def set_pin(pin_id: int, status: bool):
    """Set GPIO pin output value"""
    return gpio.set_output_pin(pin_id, status)


@api_version(0,1)
@router.patch("/{pin_id}/toggle")
async def toggle_pin(pin_id: int):
    """Toggle GPIO pin output value"""
    return gpio.toggle_output_pin(pin_id)
