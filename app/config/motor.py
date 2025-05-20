from pydantic import BaseModel, Field, model_validator
from typing import Literal, Self


class MotorConfig(BaseModel):
    direction_pin: int
    enable_pin: int
    step_pin: int

    acceleration: float
    acceleration_ramp: int
    delay: float
    direction: Literal[1, -1] = Field(1, description="Motor direction (1 or -1).")
    steps_per_rotation: int = Field(description="Number of steps for a full motor rotation.")

    min_angle: float = Field(0, ge=0, le=360, description="Minimum allowed angle for the motor in degrees.")
    max_angle: float = Field(360, ge=0, le=360, description="Maximum allowed angle for the motor in degrees.")

    @model_validator(mode='after')
    def check_min_max_angles(self) -> Self:
        if self.min_angle > self.max_angle:
            raise ValueError("min_angle cannot be greater than max_angle.")
        return self