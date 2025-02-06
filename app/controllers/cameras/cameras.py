from typing import Optional
from linuxpy.video.device import iter_video_capture_devices
import gphoto2 as gp

from app.config.camera import CameraSettings
from app.controllers.cameras.camera import CameraControllerFactory

from app.models.camera import Camera, CameraType
from app.config import config


def get_cameras() -> list[Camera]:
    return config.cameras


def get_camera_settings(camera: Camera) -> Optional[CameraSettings]:
    return camera.settings
    #return config.cameras.get(camera_id)


def get_camera(camera_id: int) -> Camera:
    cameras = config.cameras

    if len(cameras) < camera_id + 1:
        raise ValueError(f"Can't find camera with id {camera_id}")
    return cameras[camera_id]


def get_camera_controller(camera: Camera):
    return CameraControllerFactory.get_controller(camera)

