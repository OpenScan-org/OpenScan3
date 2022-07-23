import io
from v4l2py import Device

from app.controllers.cameras.camera import CameraController
from app.models.camera import Camera


class V4l2Camera(CameraController):
    @staticmethod
    def photo(camera: Camera) -> io.BytesIO:
        with Device(camera.path) as v4l2_camera:
            v4l2_camera_stream = iter(v4l2_camera)
            next(v4l2_camera_stream)  # first frame can be garbage
            return io.BytesIO(next(v4l2_camera_stream))

    @staticmethod
    def preview(camera: Camera) -> io.BytesIO:
        return V4l2Camera.photo(camera)
