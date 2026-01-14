from fastapi import APIRouter

from openscan_firmware.controllers.hardware import gpio

router = APIRouter(
    prefix="/gpio",
    tags=["gpio"],
    responses={404: {"description": "Not found"}},
)


@router.get("/")
async def get_pins() -> dict[str, list[int]]:
    """Get all initialized GPIO pins

    Returns:
        dict[str, list[int]]: A dictionary of initialized output pins and buttons
    """
    return gpio.get_initialized_pins()


@router.get("/{pin_id}", response_model=bool)
async def get_pin(pin_id: int):
    """Get output value of a specific GPIO pin

    Args:
        pin_id: The ID (int) of the GPIO pin to get the value of

    Returns:
        bool: The output value of the GPIO pin
    """
    return gpio.get_output_pin(pin_id)


@router.patch("/{pin_id}")
async def set_pin(pin_id: int, status: bool):
    """Set GPIO pin output value

    Args:
        pin_id: The ID (int) of the GPIO pin to set the value of
        status: The output value to set for the GPIO pin
    """
    return gpio.set_output_pin(pin_id, status)


@router.patch("/{pin_id}/toggle")
async def toggle_pin(pin_id: int):
    """Toggle GPIO pin output value

    Args:
        pin_id: The ID (int) of the GPIO pin to toggle
    """
    return gpio.toggle_output_pin(pin_id)
