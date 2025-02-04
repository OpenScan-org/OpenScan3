from enum import Enum
import io
from tempfile import TemporaryFile
import time
from typing import IO

from libcamera import ColorSpace, controls
ColorSpace.Jpeg = ColorSpace.Sycc
from picamera2 import Picamera2

from app.controllers.cameras.camera import CameraController, SettingsObserver, ConfigObserver
from app.models.camera import Camera, CameraMode, CameraSettings
#from app.config import config

# debug
import cv2
import numpy as np

class HardwareObserver(SettingsObserver):
    def __init__(self, controller: 'Picamera2Controller'):
        self.controller = controller

    def update(self, setting: str, value: any):
        if setting in ['resolution_photo', 'resolution_preview']:
            self.controller.restart_camera()
        self.controller.apply_settings()

class Picamera2Controller(CameraController):
    _picam = None

    def __init__(self, camera: Camera):
        super().__init__(camera)
        if Picamera2Controller._picam is None:
            Picamera2Controller._picam = Picamera2()

        self.add_observer(ConfigObserver(camera))
        self.add_observer(HardwareObserver(self))  # Add HardwareObserver here

        self.control_mapping = {
            'shutter': 'ExposureTime',
            'saturation': 'Saturation',
            'contrast': 'Contrast',
            'gain': 'AnalogueGain',
            'awbg_red': 'ColourGains',  # ColourGains is a tuple of (red gain, blue gain)
            'awbg_blue': 'ColourGains',  # ColourGains tuple of (red gain, blue gain)
        }
        if self.get_setting("jpeg_quality") is None:
            self.update_setting("jpeg_quality", 95)

        # set initial settings and start in preview mode
        self._configure_resolution()
        self.mode = CameraMode.PREVIEW
        self._picam.configure(self.preview_config)
        self._picam.start()
        self._configure_focus()

        self.apply_settings()

    def apply_settings(self):
        #self._configure_focus()

        for setting, value in self._settings.__dict__.items():
            # self.set_setting(setting, value)
            if setting in self.control_mapping:
                if setting == 'awbg_red' or setting == 'awbg_blue':
                    pass
                else:
                    self._picam.set_controls({self.control_mapping[setting]: value})

    def _configure_resolution(self):
        self.preview_resolution = self.get_setting("resolution_preview")
        if self.preview_resolution is None:
            self.preview_config = self._picam.create_preview_configuration(
                main={"size": (2328, 1748)},  # main is the default mode with higher latency but correct color
                lores={"size": (640, 480), "format": "YUV420"},  # lores is a low resolution mode with lower latency
                controls = {"NoiseReductionMode": 0, "AwbEnable": False}
            )
        else:
            self.preview_config = self._picam.create_preview_configuration(main={"size": self.preview_resolution},
                                                                           controls={"NoiseReductionMode": 0, "AwbEnable": False})

        self.photo_resolution = self.get_setting("resolution_photo")
        if self.photo_resolution is None:
            self.photo_config = self._picam.create_still_configuration( main={"size": (4656, 3496), "format": "RGB888"},
                                                                        controls={
                                                                            "NoiseReductionMode": 0,
                                                                            "AwbEnable": False})

        else:
            self.photo_config = self._picam.create_still_configuration(main={"size": self.photo_resolution})

    def _configure_focus(self):
        if self.get_setting("AF"):
            width, height = self._picam.camera_properties['PixelArraySize']

            # Get the central 1% of the image
            win_width = int(width * 0.1)
            win_height = int(height * 0.1)
            x_start = (width - win_width) // 2
            y_start = (height - win_height) // 2
            af_window = [(x_start, y_start, win_width, win_height)]

            self._picam.set_controls({
                    "AfRange": controls.AfRangeEnum.Macro,
                    "AfMetering": controls.AfMeteringEnum.Windows,
                    "AfWindows": af_window
                })

            if self.mode == CameraMode.PREVIEW:
                # Configure continuous auto  focus mode
                self._picam.set_controls({
                        "AfMode": controls.AfModeEnum.Continuous,
                        "AfSpeed": controls.AfSpeedEnum.Fast
                    })
            elif self.mode == CameraMode.PHOTO:
                # Configure continuous auto  focus mode
                self._picam.set_controls({
                        "AfMode": controls.AfModeEnum.Auto,
                        "AfSpeed": controls.AfSpeedEnum.Fast
                    })
                #self._picam.autofocus_cycle()

        else:
            # configure manual focus mode
            manual_focus = self.get_setting("manual_focus")
            if manual_focus is None:
                self.update_setting("manual_focus", 1.0)
            self._picam.set_controls({"AfMode": controls.AfModeEnum.Manual, "LensPosition": self.get_setting("manual_focus")})

        #time.sleep(0.1) # wait for focus to be applied

    def _configure_mode(self, set_mode: CameraMode = None):
        if set_mode == CameraMode.PHOTO:
            self.mode = CameraMode.PHOTO
        elif set_mode == CameraMode.PREVIEW:
            self.mode = CameraMode.PREVIEW
        self._configure_focus()

    def restart_camera(self):
        self._picam.stop()
        self._configure_resolution()
        if self.mode == CameraMode.PHOTO:
            self._picam.configure(self.photo_config)
        else:
            self._picam.configure(self.preview_config)
        self._picam.start()

    def photo(self) -> IO[bytes]:
        if self.mode == CameraMode.PREVIEW:
            self._configure_mode(CameraMode.PHOTO)
        self.apply_settings()

        self._picam.autofocus_cycle()

        array = self._picam.switch_mode_and_capture_array(self.photo_config, "main")

        array = cv2.rotate(array, cv2.ROTATE_90_COUNTERCLOCKWISE)

        _, jpeg = cv2.imencode('.jpg', array,
                               [int(cv2.IMWRITE_JPEG_QUALITY), self.get_setting("jpeg_quality")])
        return jpeg.tobytes()

    def preview(self, mode="main") -> IO[bytes]:
        if self.mode == CameraMode.PHOTO:
            self._configure_mode(CameraMode.PREVIEW)
        self.apply_settings()
        # main is the default mode with higher latency but correct color
        # lores is a low resolution mode with lower latency
        frame = self._picam.capture_array(mode)
        if mode == "lores":
            # color and gamma correction
            frame = cv2.cvtColor(frame, cv2.COLOR_YUV420p2RGB)
            frame = apply_gamma_correction(frame, gamma=2.2)
        elif mode == "main":
            frame = cv2.resize(frame, (640,480))
            frame = frame[:, :, [2, 1, 0]] # correct color order

        frame = cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)

        focus_position = self._picam.capture_metadata()["LensPosition"]
        print("Current Focus position: ", focus_position)

        _, jpeg = cv2.imencode('.jpg', frame)
        return jpeg.tobytes()


def apply_gamma_correction(image, gamma=2.2):
    lookup_table = np.array([((i / 255.0) ** (1.0 / gamma)) * 255 for i in range(256)]).astype("uint8")
    return cv2.LUT(image, lookup_table)