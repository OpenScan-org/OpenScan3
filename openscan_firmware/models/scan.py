from datetime import datetime
from time import time
from dataclasses import field
from typing import Optional

from pydantic import BaseModel, Field, model_validator

from openscan_firmware.config.scan import ScanSetting
from openscan_firmware.config.camera import CameraSettings
from openscan_firmware.models.paths import PolarPoint3D, CartesianPoint3D
from openscan_firmware.utils.paths.paths import polar_to_cartesian
from openscan_firmware.models.task import TaskStatus

class StackingTaskStatus(BaseModel):
    task_id: Optional[str] = None
    status: Optional[TaskStatus] = None

class Scan(BaseModel):
    """Represents a single scan session within a project."""
    project_name: str = Field(..., description="The name of the project this scan belongs to.")
    index: int = Field(..., description="The sequential index of the scan within the project.")
    created: datetime = Field(default_factory=datetime.now, description="The timestamp when the scan was created.")
    status: TaskStatus = Field(
        default=TaskStatus.PENDING,
        description="The final, persistent status of the scan, mirroring the associated Task status.",
    )

    settings: ScanSetting = Field(..., description="The settings used for this scan.")
    camera_name: Optional[str] = None
    camera_settings: CameraSettings = Field(..., description="The camera settings used for this scan.")

    current_step: int = 0
    system_message: Optional[str] = None
    last_updated: datetime = field(default_factory=datetime.now)

    description: Optional[str] = None

    duration: float = 0.0
    total_size_bytes: int = Field(
        default=0,
        ge=0,
        description="Total size of all files belonging to the scan, in bytes.",
    )
    photos: list[str] = Field(
        default_factory=list,
        description="Relative filenames (with extension) of all photos captured for this scan.",
    )

    task_id: Optional[str] = None

    stacking_task_status: Optional[StackingTaskStatus] = None


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