from typing import Optional

from app.config.camera import CameraSettings
from controllers.hardware.cameras.camera import CameraControllerFactory

from app.models.camera import Camera
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

