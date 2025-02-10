from tempfile import TemporaryFile
from typing import IO
from linuxpy.video.device import Device, VideoCapture

from controllers.hardware.cameras.camera import CameraController
from app.models.camera import Camera, CameraMode


class LINUXPYCamera(CameraController):
    __camera = [None, None]

    @classmethod
    def _get_camera(cls, camera: Camera, mode: CameraMode) -> Device:
        if cls.__camera[1] != mode:
            cls.__camera[1] = mode
            if cls.__camera[0] is not None:
                cls.__camera[0].close()
            cls.__camera[0] = Device(camera.path)
            if mode == CameraMode.PHOTO:
                # set mode
                with cls.__camera[0] as camera:
                    capture = VideoCapture(camera)
                    capture.set_format(1920, 1080, "MJPG")
                    #with capture:
                    #    for frame in capture:
                #cls.__camera[0].video_capture.set_format(1920, 1080, "MJPG")
            elif mode == CameraMode.PREVIEW:
                cls.__camera[0].video_capture.set_format(320, 240, "MJPG")
        return cls.__camera[0]

    @staticmethod
    def photo(camera: Camera) -> IO[bytes]:
        linuxpy_camera = LINUXPYCamera._get_camera(camera, CameraMode.PHOTO)
        with linuxpy_camera as camera:
            capture = VideoCapture(camera)
            capture.set_format(1920, 1080, "MJPG")
            with capture:
                for frame in capture:
                    file = TemporaryFile()
                    file.write(bytes(frame))
                    file.seek(0)
                    return file

    @staticmethod
    def preview(camera: Camera) -> IO[bytes]:
        # not working yet!
        linuxpy_camera = LINUXPYCamera._get_camera(camera, CameraMode.PREVIEW)
        linuxpy_camera_stream = iter(linuxpy_camera)
        next(linuxpy_camera_stream)  # first frame can be garbage
        file = TemporaryFile()
        file.write(next(linuxpy_camera_stream))
        file.seek(0)
        return file
