from datetime import datetime
from time import time
from dataclasses import field
from typing import Optional
from enum import Enum

from pydantic import BaseModel, Field, model_validator

from app.config.scan import ScanSetting
from app.config.camera import CameraSettings
from app.models.paths import PolarPoint3D, CartesianPoint3D
from app.utils.paths.paths import polar_to_cartesian


class ScanStatus(str, Enum):
    """Defines the persistent status of a scan."""
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
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

    #photos: list[str] = field(default_factory=list)

    task_id: Optional[str] = None


class ScanMetadata(BaseModel):
    """Represents metadata from a scan for a photo."""
    step: int = Field(..., description="The sequential index of the photo within the scan.")
    polar_coordinates: PolarPoint3D = Field(..., description="The polar coordinates of the camera when the photo was taken.")
    project_name: str = Field(..., description="The name of the project this scan belongs to.")
    scan_index: int = Field(..., description="The sequential index of the scan within the project.")
    stack_index: Optional[int] = Field(None, description="The sequential index of the photo within the focus stack.")
    cart_coordinates: Optional[CartesianPoint3D] = Field(None, description="Cartesian coordinates, derived from polar_coordinates.")

    @model_validator(mode="after")
    def set_cart_coordinates(self) -> "ScanMetadata":
        self.cart_coordinates = polar_to_cartesian(self.polar_coordinates)
        return self