from dataclasses import dataclass
from enum import Enum
from typing import Optional

from app.config.camera import CameraSettings

class CameraMode(Enum):
    PHOTO = "photo"
    PREVIEW = "preview"

class CameraType(Enum):
    GPHOTO2 = "gphoto2"
    V4L2 = "v4l2"
    PICAMERA2 = "picamera2"
    EXTERNAL = "external"


@dataclass
class Camera:
    type: CameraType
    name: str
    path: str

    settings: Optional[CameraSettings]
