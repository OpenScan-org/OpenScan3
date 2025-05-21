from pydantic import BaseModel
from typing import Optional


class EndstopConfig(BaseModel):
    pin: int
    angular_position: float
    motor_name: str

    pull_up: Optional[bool] = True
    bounce_time: Optional[float] = 0.005