from enum import Enum
from pydantic import BaseModel

from models.camera import Camera
from models.light import Light
from models.motor import Motor


class ScannerModel(Enum):
    CLASSIC = "classic"
    MINI = "mini"

class ScannerShield(Enum):
    GREENSHIELD = "greenshield"
    BLACKSHIELD = "blackshield"

class ScannerDevice(BaseModel):
    name: str
    model: ScannerModel
    shield: ScannerShield
    cameras: dict[str, Camera]
    motors: dict[str, Motor]
    lights: dict[str, Light]