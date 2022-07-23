import io
import time

from picamera2 import Picamera2

from app.controllers.cameras.camera import CameraController
from app.models.camera import Camera


class Picamera2Camera(CameraController):
    @staticmethod
    def photo(camera: Camera) -> io.BytesIO:
        picam2 = Picamera2()
        picam2.configure(picam2.create_still_configuration())
        picam2.start()
        data = io.BytesIO()
        time.sleep(1)
        picam2.capture_file(data, format='jpeg')
        data.seek(0)
        picam2.close()
        return data


    @staticmethod
    def preview(camera: Camera) -> io.BytesIO:
        return Picamera2Camera.photo(camera)
