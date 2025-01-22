from typing import Optional
from linuxpy.video.device import iter_video_capture_devices
import gphoto2 as gp

from app.config.camera import CameraSettings
from app.controllers.cameras.camera import CameraController
from app.controllers.cameras.gphoto2 import Gphoto2Camera
from app.controllers.cameras.picamera2 import Picamera2Camera
from app.controllers.cameras.linuxpy import LINUXPYCamera
from app.models.camera import Camera, CameraType
from app.config import config


def get_linuxpy_cameras() -> list[Camera]:
    linuxpy_cameras = iter_video_capture_devices()
    for cam in linuxpy_cameras:
        cam.open()
        if cam.info.card not in ("unicam", "bcm2835-isp"):
            name = cam.info.card
            path = cam.filename
            settings = get_camera_settings(cam.info.card)
        cam.close()
    return [Camera(
            type=CameraType.LINUXPY,
            name=name,
            path=path,
            settings=settings
        )]

def get_gphoto2_cameras() -> list[Camera]:
    gphoto2_cameras = gp.Camera.autodetect()
    return [
        Camera(
            type=CameraType.GPHOTO2,
            name=c[0],
            path=c[1],
            settings=get_camera_settings(c[0]),
        )
        for c in gphoto2_cameras
    ]

def get_picameras() -> list[Camera]:
    linuxpy_cameras = iter_video_capture_devices()
    for cam in linuxpy_cameras:
        cam.open()
        if cam.info.card == "unicam":
            name = cam.info.card
            path = cam.filename
            settings = get_camera_settings(cam.info.card)
        cam.close()
    return [Camera(
            type=CameraType.PICAMERA2,
            name=name,
            path=path,
            settings=settings
        )]

def get_cameras() -> list[Camera]:
    cameras = []
    cameras.extend(get_linuxpy_cameras())
    cameras.extend(get_gphoto2_cameras())
    cameras.extend(get_picameras())
    return cameras


def get_camera_settings(camera_id: str) -> Optional[CameraSettings]:
    return config.cameras.get(camera_id)


def get_camera(camera_id: int) -> Camera:
    cameras = get_cameras()

    if len(cameras) < camera_id + 1:
        raise ValueError(f"Can't find camera with id {camera_id}")
    return cameras[camera_id]


def get_camera_controller(camera: Camera) -> type[CameraController]:
    if camera.type == CameraType.GPHOTO2:
        return Gphoto2Camera
    elif camera.type == CameraType.PICAMERA2:
        return Picamera2Camera
    elif camera.type == CameraType.LINUXPY:
        return LINUXPYCamera

    raise ValueError(f"Couldn't find controller for {camera.type}")
