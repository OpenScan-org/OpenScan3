from datetime import datetime
from time import time
from dataclasses import field
from typing import Optional
from enum import Enum

from pydantic import BaseModel, Field

from app.config.scan import ScanSetting
from app.config.camera import CameraSettings


class ScanStatus(str, Enum):
    """Defines the persistent status of a scan."""
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class Scan(BaseModel):
    """Represents a single scan session within a project."""
    project_name: str = Field(..., description="The name of the project this scan belongs to.")
    index: int = Field(..., description="The sequential index of the scan within the project.")
    created: datetime = Field(default_factory=datetime.now, description="The timestamp when the scan was created.")
    status: ScanStatus = Field(default=ScanStatus.PENDING, description="The final, persistent status of the scan.")

    settings: ScanSetting = Field(..., description="The settings used for this scan.")
    camera_name: Optional[str] = None
    camera_settings: CameraSettings = Field(..., description="The camera settings used for this scan.")

    current_step: int = 0
    system_message: Optional[str] = None
    last_updated: datetime = field(default_factory=datetime.now)

    description: Optional[str] = None

    duration: float = 0.0

    photos: list[str] = field(default_factory=list)

    task_id: Optional[str] = None