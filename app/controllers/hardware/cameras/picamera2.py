import time

import cv2
import numpy as np
from typing import IO
from libcamera import ColorSpace, controls, Transform
ColorSpace.Jpeg = ColorSpace.Sycc
from picamera2 import Picamera2

from app.controllers.hardware.cameras.camera import CameraController
from app.models.camera import Camera, CameraMode
from app.config.camera import CameraSettings

class CameraStrategy:
    """Base strategy class for camera-specific configurations and processing"""
    def create_preview_config(self, picam: Picamera2, preview_resolution=None):
        raise NotImplementedError

    def create_photo_config(self, picam: Picamera2, photo_resolution=None):
        raise NotImplementedError

    def process_preview_frame(self, frame):
        return frame

    def process_photo_frame(self, frame):
        return frame

class IMX519Strategy(CameraStrategy):
    def create_preview_config(self, picam: Picamera2, preview_resolution=None):
        if preview_resolution is None:
            return picam.create_preview_configuration(
                main={"size": (2328, 1748)},
                lores={"size": (640, 480), "format": "YUV420"},
                controls={"NoiseReductionMode": 0, "AwbEnable": False}
            )
        return picam.create_preview_configuration(
            main={"size": preview_resolution},
            controls={"NoiseReductionMode": 0, "AwbEnable": False}
        )

    def create_photo_config(self, picam: Picamera2, photo_resolution=None):
        if photo_resolution is None:
            return picam.create_still_configuration(
                main={"size": (4656, 3496), "format": "RGB888"},
                controls={"NoiseReductionMode": 0, "AwbEnable": False}
            )
        return picam.create_still_configuration(buffer_count=1, main={"size": photo_resolution})

class HawkeyeStrategy(CameraStrategy):
    def create_preview_config(self, picam: Picamera2, preview_resolution=None):
        return picam.create_preview_configuration(
            transform=Transform(hflip=True, vflip=True),
            main={"size": (2328, 1748)},
            controls={"NoiseReductionMode": 0, "AwbEnable": False}
        )

    def create_photo_config(self, picam: Picamera2, photo_resolution=None):
        if photo_resolution is None:
            return picam.create_still_configuration(
                buffer_count=1,
                transform=Transform(hflip=True, vflip=True),
                main={"size": (8000, 6000)}
            )
        return picam.create_still_configuration(buffer_count=1, main={"size": photo_resolution})

    def process_preview_frame(self, frame):
        return frame[:, :, [2, 1, 0]]  # correct the color channels


    def process_photo_frame(self, frame):
        return frame[:, :, [2, 1, 0]]  # correct the color channels

class Picamera2Controller(CameraController):
    _picam = None
    _strategies = {
        "imx519": IMX519Strategy(),
        "arducam_64mp": HawkeyeStrategy()
    }

    def __init__(self, camera: Camera):
        super().__init__(camera)
        if Picamera2Controller._picam is None:
            Picamera2Controller._picam = Picamera2()
        self.strategy = self._strategies.get(self.camera.name, IMX519Strategy())

        self.control_mapping = {
            'shutter': 'ExposureTime',
            'saturation': 'Saturation',
            'contrast': 'Contrast',
            'gain': 'AnalogueGain',
            'awbg_red': 'ColourGains',  # ColourGains is a tuple of (red gain, blue gain)
            'awbg_blue': 'ColourGains',  # ColourGains tuple of (red gain, blue gain)
        }

        if self.settings_manager.get_setting("jpeg_quality") is None:
            self.settings_manager.set_setting("jpeg_quality", 95)

        self._configure_resolution()
        self.mode = CameraMode.PREVIEW
        self._picam.configure(self.preview_config)
        self._picam.start()

        self._apply_settings_to_hardware(self.get_all_settings())

    def _apply_settings_to_hardware(self, settings: CameraSettings):
        """This method is call on every change of settings"""
        self._configure_focus()
        
        # apply all settings
        for setting, value in settings.__dict__.items():
            if setting in self.control_mapping:
                if setting not in ['awbg_red', 'awbg_blue']:  # AWB gains are set separately
                    self._picam.set_controls({self.control_mapping[setting]: value})
                else:
                    # TODO implement settings for AWB
                    pass


    def _configure_resolution(self):
        self.preview_resolution = self.get_setting("resolution_preview")
        self.preview_config = self.strategy.create_preview_config(self._picam, self.preview_resolution)

        self.photo_resolution = self.get_setting("resolution_photo")
        self.photo_config = self.strategy.create_photo_config(self._picam, self.photo_resolution)
        self.preview_resolution = self.get_setting("resolution_preview")

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
                self.set_setting("manual_focus", 1.0)
            self._picam.set_controls({"AfMode": controls.AfModeEnum.Manual, "LensPosition": self.get_setting("manual_focus")})
            start = time.time()
            while self._picam.capture_metadata()["LensPosition"] != self.get_setting("manual_focus"):
                #print(self._picam.capture_metadata()["LensPosition"])
                time.sleep(0.05) # wait for focus to be applied
            print("focus applied in: {:.2f}".format(time.time() - start), " focus: ", self.get_setting("manual_focus"))
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
        if self.get_setting("AF"):
            self._picam.autofocus_cycle()

        array = self._picam.switch_mode_and_capture_array(self.photo_config, "main")
        array = cv2.rotate(array, cv2.ROTATE_90_COUNTERCLOCKWISE)
        array = self.strategy.process_photo_frame(array)

        _, jpeg = cv2.imencode('.jpg', array,
                               [int(cv2.IMWRITE_JPEG_QUALITY), self.get_setting("jpeg_quality")])
        return jpeg.tobytes()

    def preview(self, mode="main") -> IO[bytes]:
        if self.mode == CameraMode.PHOTO:
            self._configure_mode(CameraMode.PREVIEW)
        frame = self._picam.capture_array(mode)
        if mode == "lores":
            frame = cv2.cvtColor(frame, cv2.COLOR_YUV420p2RGB)
            frame = apply_gamma_correction(frame, gamma=2.2)
        elif mode == "main":
            frame = cv2.resize(frame, (640,480))
            frame = self.strategy.process_preview_frame(frame)

        frame = cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)

        #focus_position = self._picam.capture_metadata()["LensPosition"]
        #print("Current Focus position: ", focus_position)

        _, jpeg = cv2.imencode('.jpg', frame)
        return jpeg.tobytes()


def apply_gamma_correction(image, gamma=2.2):
    lookup_table = np.array([((i / 255.0) ** (1.0 / gamma)) * 255 for i in range(256)]).astype("uint8")
    return cv2.LUT(image, lookup_table)