from typing import Optional
from pydantic import BaseModel

from app.config.motor import MotorConfig


class Motor(BaseModel):
    name: str
    settings: Optional[MotorConfig]
    angle: float = 90.0

