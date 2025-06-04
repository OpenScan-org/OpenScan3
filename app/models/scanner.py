from enum import Enum
from typing import Optional

from pydantic import BaseModel

from app.models.camera import Camera
from app.models.light import Light
from app.models.motor import Motor, Endstop


class ScannerModel(Enum):
    CLASSIC = "classic"
    MINI = "mini"

class ScannerShield(Enum):
    GREENSHIELD = "greenshield"
    BLACKSHIELD = "blackshield"

class ScannerDevice(BaseModel):
    name: str
    model: Optional[ScannerModel]
    shield: Optional[ScannerShield]
    cameras: dict[str, Camera]
    motors: dict[str, Motor]
    lights: dict[str, Light]
    endstops: Optional[dict[str, Endstop]]

    initialized: bool