from enum import Enum
from typing import Optional

from pydantic import BaseModel, PrivateAttr, ConfigDict, Field

from openscan_firmware.config.camera import CameraSettings
from openscan_firmware.config.endstop import EndstopConfig
from openscan_firmware.config.light import LightConfig
from openscan_firmware.config.motor import MotorConfig
from openscan_firmware.models.camera import Camera, CameraType
from openscan_firmware.models.light import Light
from openscan_firmware.models.motor import Motor, Endstop

class ScannerModel(Enum):
    CLASSIC = "classic"
    MINI = "mini"
    CUSTOM = "custom"

class ScannerShield(Enum):
    GREENSHIELD = "greenshield"
    BLACKSHIELD = "blackshield"
    CUSTOM = "custom"
    
class ScannerStartupMode(Enum):
    STARTUP_IDLE = "startup_idle"
    STARTUP_ENABLED = "startup_enabled"
    
class ScannerCalibrateMode(Enum):
    CALIBRATE_MANUAL = "calibrate_manual"
    CALIBRATE_ON_HOME = "calibrate_on_home"
    CALIBRATE_ON_SCAN = "calibrate_on_scan"
    CALIBRATE_ON_WAKE = "calibrate_on_wake"

class ScannerDevice(BaseModel):
    # this is the default, but explicit is clearer
    model_config = ConfigDict(extra="ignore")

    name: str
    model: Optional[ScannerModel]
    shield: Optional[ScannerShield]
    cameras: dict[str, Camera]
    motors: dict[str, Motor]
    lights: dict[str, Light]
    endstops: Optional[dict[str, Endstop]]
    
    # motors timeout in seconds - 0 to disable
    motors_timeout: float = 0.0
    
    startup_mode: ScannerStartupMode = ScannerStartupMode.STARTUP_ENABLED
    calibrate_mode: ScannerCalibrateMode = ScannerCalibrateMode.CALIBRATE_MANUAL
    
    _idle : bool = PrivateAttr(default=False)
    _initialized: bool = PrivateAttr(default=False)


class PersistedCameraConfig(BaseModel):
    type: CameraType | str
    path: str
    settings: CameraSettings = Field(default_factory=CameraSettings)


class PersistedEndstopConfig(BaseModel):
    settings: EndstopConfig


class ScannerDeviceConfig(BaseModel):
    """Persisted scanner configuration payload stored as JSON."""

    model_config = ConfigDict(extra="ignore")

    name: str
    model: str | None = None
    shield: str | None = None
    cameras: dict[str, PersistedCameraConfig] = Field(default_factory=dict)
    motors: dict[str, MotorConfig] = Field(default_factory=dict)
    lights: dict[str, LightConfig] = Field(default_factory=dict)
    endstops: dict[str, PersistedEndstopConfig] | None = None
    motors_timeout: float = 0.0
    startup_mode: ScannerStartupMode | str = ScannerStartupMode.STARTUP_ENABLED
    calibrate_mode: ScannerCalibrateMode | str = ScannerCalibrateMode.CALIBRATE_MANUAL
