from dataclasses import dataclass
from enum import Enum
from typing import Optional

from app.config.motor import MotorConfig


@dataclass
class Motor:
    name: str
    settings: Optional[MotorConfig]
    angle: float = 0

