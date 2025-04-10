from datetime import datetime
from time import time
from dataclasses import field
from typing import Optional

from pydantic.dataclasses import dataclass
from enum import Enum

from app.config.scan import ScanSetting
from app.config.camera import CameraSettings

class ScanStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ERROR = "error"

@dataclass
class Scan:
    project_name: str
    index: int

    created: datetime

    settings: ScanSetting
    camera_settings: CameraSettings

    status: ScanStatus = ScanStatus.PENDING
    current_step: int = 0
    system_message: Optional[str] = None
    last_updated: datetime = field(default_factory=datetime.now)

    description: str = None

    duration: float = 0.0

    photos: list[str] = field(default_factory=list)