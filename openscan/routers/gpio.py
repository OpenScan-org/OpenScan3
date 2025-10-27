from fastapi import APIRouter

from openscan.controllers.hardware import gpio

router = APIRouter(
    prefix="/gpio",
    tags=["gpio"],
    responses={404: {"description": "Not found"}},
)


@router.get("/")
async def get_pins():
    """Get all initialized GPIO pins"""
    return gpio.get_initialized_pins()


@router.get("/{pin_id}", response_model=bool)
async def get_pin(pin_id: int):
    """Get output value of a specific GPIO pin"""
    return gpio.get_output_pin(pin_id)


@router.patch("/{pin_id}")
async def set_pin(pin_id: int, status: bool):
    """Set GPIO pin output value"""
    return gpio.set_output_pin(pin_id, status)


@router.patch("/{pin_id}/toggle")
async def toggle_pin(pin_id: int):
    """Toggle GPIO pin output value"""
    return gpio.toggle_output_pin(pin_id)
