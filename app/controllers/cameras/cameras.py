from typing import Optional
from v4l2py.device import iter_video_capture_devices
import gphoto2 as gp

from app.config.camera import CameraSettings
from app.controllers.cameras.camera import CameraController
from app.controllers.cameras.gphoto2 import Gphoto2Camera
from app.controllers.cameras.picamera2 import Picamera2Camera
from app.controllers.cameras.v4l2 import V4l2Camera
from app.models.camera import Camera, CameraType
from app.config import config


def get_cameras() -> list[Camera]:
    cameras = []
    v4l2_cameras = iter_video_capture_devices()
    cameras.extend(
        [
            Camera(
                type=CameraType.V4L2,
                name=c.info.card,
                path=str(c.filename),
                settings=get_camera_settings(c.info.card),
            )
            for c in v4l2_cameras
            if c.info.card not in ("unicam", "bcm2835-isp")
        ]
    )
    gphoto2_cameras = gp.Camera.autodetect()
    cameras.extend(
        [
            Camera(
                type=CameraType.GPHOTO2,
                name=c[0],
                path=c[1],
                settings=get_camera_settings(c[0]),
            )
            for c in gphoto2_cameras
        ]
    )

    picameras = iter_video_capture_devices()
    cameras.extend(
        [
            Camera(
                type=CameraType.PICAMERA2,
                name=c.info.card,
                path=str(c.filename),
                settings=get_camera_settings(c.info.card),
            )
            for c in picameras
            if c.info.card == "unicam"
        ]
    )

    

    return cameras


def get_camera_settings(camera_id: str) -> Optional[CameraSettings]:
    return config.cameras.get(camera_id)


def get_camera(camera_id: int) -> Camera:
    cameras = get_cameras()

    if len(cameras) < camera_id + 1:
        raise ValueError(f"Can't find camera with id {camera_id}")
    return cameras[camera_id]


def get_camera_controller(camera: Camera) -> type[CameraController]:
    if camera.type == CameraType.V4L2:
        return V4l2Camera
    elif camera.type == CameraType.GPHOTO2:
        return Gphoto2Camera
    elif camera.type == CameraType.PICAMERA2:
        return Picamera2Camera

    raise ValueError(f"Couldn't find controller for {camera.type}")
