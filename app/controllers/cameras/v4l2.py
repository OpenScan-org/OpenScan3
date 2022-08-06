import io
from tempfile import TemporaryFile
from typing import IO
from v4l2py import Device

from app.controllers.cameras.camera import CameraController
from app.models.camera import Camera


class V4l2Camera(CameraController):
    @classmethod
    def _get_camera(cls, camera: Camera) -> Device:
        if cls._camera is None:
            cls._camera = Device(camera.path)
        return cls._camera

    @staticmethod
    def photo(camera: Camera) -> IO[bytes]:
        v4l2_camera = V4l2Camera._get_camera(camera)
        v4l2_camera_stream = iter(v4l2_camera)
        next(v4l2_camera_stream)  # first frame can be garbage
        file = TemporaryFile()
        file.write(next(v4l2_camera_stream))
        return file

    @staticmethod
    def preview(camera: Camera) -> IO[bytes]:
        return V4l2Camera.photo(camera)
