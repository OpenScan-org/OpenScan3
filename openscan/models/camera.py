import io
from enum import Enum
from typing import Literal, Union, Optional
import numpy as np
from pydantic import BaseModel, Field, ConfigDict

from openscan.config.camera import CameraSettings
from openscan.models.scan import ScanMetadata

class CameraMode(Enum):
    PHOTO = "photo"
    PREVIEW = "preview"

class CameraType(Enum):
    GPHOTO2 = "gphoto2"
    LINUXPY = "linuxpy"
    PICAMERA2 = "picamera2"
    EXTERNAL = "external"


class Camera(BaseModel):
    type: CameraType
    name: str
    path: str

    settings: CameraSettings

    #mode: Optional[CameraMode] = None

class CameraMetadata(BaseModel):
    """Represents metadata from a camera."""
    camera_name: str
    camera_settings: CameraSettings
    raw_metadata: dict

class PhotoData(BaseModel):
    """Represents a photo taken by the camera."""
    data: Union[io.BytesIO, np.ndarray] = Field(
        ...,
        description="Image data (JPEG/DNG) or as numpy array"
    )
    format:  Literal['jpeg','dng','rgb_array', 'yuv_array']
    camera_metadata: CameraMetadata
    scan_metadata: Optional[ScanMetadata] = None

    model_config = ConfigDict(arbitrary_types_allowed=True)
