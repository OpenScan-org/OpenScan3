from dataclasses import dataclass


@dataclass
class MotorConfig:
    direction_pin: int
    enable_pin: int
    step_pin: int

    acceleration: int
    acceleration_ramp: int
    delay: int
    direction: int
    steps_per_rotation: int
