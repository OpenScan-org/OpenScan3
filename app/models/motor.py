from dataclasses import dataclass
from enum import Enum
from typing import Optional

from app.config.motor import MotorConfig

class MotorType(Enum):
    TURNTABLE = "turntable"
    ROTOR = "rotor"

@dataclass
class Motor:
    name: str
    settings: Optional[MotorConfig]

    angle: float = 0

