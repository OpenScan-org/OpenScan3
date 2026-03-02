from typing import Optional
from pydantic import BaseModel, PrivateAttr

from openscan_firmware.config.motor import MotorConfig
from openscan_firmware.config.endstop import EndstopConfig


class Motor(BaseModel):
    name: str
    settings: Optional[MotorConfig]
    angle: float = 90.0
    _calibrated: bool = PrivateAttr(default=False)
    

class Endstop(BaseModel):
    name: str
    settings: Optional[EndstopConfig]
