from typing import Optional
from pydantic import BaseModel

from openscan_firmware.config.motor import MotorConfig
from openscan_firmware.config.endstop import EndstopConfig


class Motor(BaseModel):
    name: str
    settings: Optional[MotorConfig]
    angle: float = 90.0

class Endstop(BaseModel):
    name: str
    settings: Optional[EndstopConfig]