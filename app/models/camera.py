from enum import Enum
from typing import Optional

from pydantic import BaseModel

from app.config.camera import CameraSettings

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

