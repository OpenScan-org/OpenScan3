from pydantic import BaseModel, Field
from typing import Optional


class EndstopConfig(BaseModel):
    """
    Configuration for a motor endstop.

    Args:
        pin (int): GPIO pin number used for the endstop.
        angular_position (float): Angle at which the endstop is triggered (degrees).
        motor_name (str): Name of the assigned motor.
        pull_up (Optional[bool]): Whether to use a pull-up resistor (default: True).
        bounce_time (Optional[float]): Debounce time for the button in seconds (default: 0.005).
    """
    pin: int = Field(..., description="GPIO pin number used for the endstop")
    angular_position: float = Field(..., description="Angle at which the endstop is triggered (degrees)")
    motor_name: str = Field(..., description="Name of the assigned motor")

    pull_up: Optional[bool] = Field(True, description="Whether to use a pull-up resistor")
    bounce_time: Optional[float] = Field(0.005, description="Debounce time for the button in seconds")
