from enum import Enum
import io
from tempfile import TemporaryFile
import time
from typing import IO
from .picamera2_focus import Focuser

from libcamera import ColorSpace
ColorSpace.Jpeg = ColorSpace.Sycc
from picamera2 import Picamera2

from app.controllers.cameras.camera import CameraController
from app.models.camera import Camera, CameraMode

isTrue = lambda v : (v is True or v.lower() in ("yes", "true", "t")) if v is not None else None
isFalse = lambda v : (v is False or v.lower() in ("no", "false", "f")) if v is not None else None


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
    def photo(camera: Camera, focus=None) -> IO[bytes]:
        data = TemporaryFile()
        picam2 = Picamera2Camera._get_camera(camera, CameraMode.PHOTO)

        if (focus is True):
            picam2.set_controls({"AfTrigger":1})
        elif (focus is False):
            picam2.set_controls({"AfTrigger":0})

        picam2.capture_file(data, format='jpeg')
        data.seek(0)
        return data


    @staticmethod
    def preview(camera: Camera, focus=None) -> IO[bytes]:

        picam2 = Picamera2Camera._get_camera(camera, CameraMode.PREVIEW)
        
        if (isTrue(focus)):
            picam2.set_controls({"AfTrigger":1})
        elif (isFalse(focus)):
            picam2.set_controls({"AfTrigger":0})
        
        while True:
            data = TemporaryFile()
            picam2.capture_file(data, format='jpeg')
            data.seek(0)
            yield data
            data.close()
            
        # return Picamera2Camera.photo(camera)

    def focus(camera: Camera, value=None) -> int:
        if value is not None:
            Focuser('/dev/v4l-subdev1').write(value= value)
            return value

        return Focuser('/dev/v4l-subdev1').read()
