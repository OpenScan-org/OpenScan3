from enum import Enum
import io
from tempfile import TemporaryFile
import time
from typing import IO

from picamera2 import Picamera2

from app.controllers.cameras.camera import CameraController
from app.models.camera import Camera

class Picamera2Mode(Enum):
    PHOTO = "photo"
    PREVIEW = "preview"


class Picamera2Camera(CameraController):

    _camera = [None, None]

    @classmethod
    def _get_camera(cls, camera: Camera, mode: Picamera2Mode) -> Picamera2:
        if cls._camera[1] != mode:
            cls._camera[1] = mode
            if cls._camera[0]:
                cls._camera[0].stop()
            else:
                cls._camera[0] = Picamera2()
            if mode == Picamera2Mode.PHOTO:
                cls._camera[0].configure(cls._camera[0].create_still_configuration())
            elif mode == Picamera2Mode.PREVIEW:
                cls._camera[0].configure(cls._camera[0].create_preview_configuration(buffer_count=2,  main={"size": (640, 480)}))
            cls._camera[0].start()
        return cls._camera[0]

    @staticmethod
    def photo(camera: Camera) -> IO[bytes]:
        data = TemporaryFile()
        picam2 = Picamera2Camera._get_camera(camera, Picamera2Mode.PHOTO)
        picam2.capture_file(data, format='jpeg')
        data.seek(0)
        return data


    @staticmethod
    def preview(camera: Camera) -> IO[bytes]:
        data = TemporaryFile()
        picam2 = Picamera2Camera._get_camera(camera, Picamera2Mode.PREVIEW)
        picam2.capture_file(data, format='jpeg')
        data.seek(0)
        return data
        # return Picamera2Camera.photo(camera)
