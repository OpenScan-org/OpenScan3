from enum import Enum
import io
from tempfile import TemporaryFile
import time
from typing import IO

from picamera2 import Picamera2

from app.controllers.cameras.camera import CameraController
from app.models.camera import Camera, CameraMode


class Picamera2Camera(CameraController):
    __camera = [None, None]

    @classmethod
    def _get_camera(cls, camera: Camera, mode: CameraMode) -> Picamera2:
        if cls.__camera[1] != mode:
            cls.__camera[1] = mode
            if cls.__camera[0]:
                cls.__camera[0].stop()
            else:
                cls.__camera[0] = Picamera2()
            if mode == CameraMode.PHOTO:
                cls.__camera[0].configure(cls.__camera[0].create_still_configuration())
            elif mode == CameraMode.PREVIEW:
                cls.__camera[0].configure(cls.__camera[0].create_preview_configuration(buffer_count=2,  main={"size": (640, 480)}))
            cls.__camera[0].start()
        return cls.__camera[0]

    @staticmethod
    def photo(camera: Camera) -> IO[bytes]:
        data = TemporaryFile()
        picam2 = Picamera2Camera._get_camera(camera, CameraMode.PHOTO)
        picam2.capture_file(data, format='jpeg')
        data.seek(0)
        return data


    @staticmethod
    def preview(camera: Camera) -> IO[bytes]:
        data = TemporaryFile()
        picam2 = Picamera2Camera._get_camera(camera, CameraMode.PREVIEW)
        picam2.capture_file(data, format='jpeg')
        data.seek(0)
        return data
        # return Picamera2Camera.photo(camera)
