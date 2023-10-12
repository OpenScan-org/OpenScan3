import io
from tempfile import TemporaryFile
from typing import IO
from v4l2py import Device

from app.controllers.cameras.camera import CameraController
from app.models.camera import Camera, CameraMode


class V4l2Camera(CameraController):
    __camera = [None, None]

    @classmethod
    def _get_camera(cls, camera: Camera, mode: CameraMode) -> Device:
        if cls.__camera[1] != mode:
            cls.__camera[1] = mode
            if cls.__camera[0] is not None:
                cls.__camera[0].close()
            cls.__camera[0] = Device(camera.path)
            if mode == CameraMode.PHOTO:
                cls.__camera[0].video_capture.set_format(1920, 1080, "MJPG")
            elif mode == CameraMode.PREVIEW:
                cls.__camera[0].video_capture.set_format(320, 240, "MJPG")
        return cls.__camera[0]

    @staticmethod
    def photo(camera: Camera) -> IO[bytes]:
        v4l2_camera = V4l2Camera._get_camera(camera, CameraMode.PHOTO)
        v4l2_camera_stream = iter(v4l2_camera)
        next(v4l2_camera_stream)  # first frame can be garbage
        file = TemporaryFile()
        file.write(next(v4l2_camera_stream))
        file.seek(0)
        return file

    @staticmethod
    def preview(camera: Camera) -> IO[bytes]:
        v4l2_camera = V4l2Camera._get_camera(camera, CameraMode.PREVIEW)
        v4l2_camera_stream = iter(v4l2_camera)
        next(v4l2_camera_stream)  # first frame can be garbage
        file = TemporaryFile()
        file.write(next(v4l2_camera_stream))
        file.seek(0)
        return file
