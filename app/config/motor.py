from pydantic import BaseModel

class MotorConfig(BaseModel):
    direction_pin: int
    enable_pin: int
    step_pin: int

    acceleration: int
    acceleration_ramp: int
    delay: float
    direction: int
    steps_per_rotation: int
