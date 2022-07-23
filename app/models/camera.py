from dataclasses import dataclass
from enum import Enum
from typing import Optional

from app.config.camera import CameraSettings


class CameraType(Enum):
    GPHOTO2 = 0
    V4L2 = 1
    PICAMERA2 = 2
    EXTERNAL = 3


@dataclass
class Camera:
    type: CameraType
    name: str
    path: str

    settings: Optional[CameraSettings]
