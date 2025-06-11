"""
Picamera2 Controller

This module provides a CameraController class for controlling the picamera2 camera.

"""

import logging
import time

import cv2
import numpy as np
from typing import IO
from libcamera import ColorSpace, controls, Transform
ColorSpace.Jpeg = ColorSpace.Sycc
from picamera2 import Picamera2

from app.controllers.hardware.cameras.camera import CameraController
from app.models.camera import Camera
from app.config.camera import CameraSettings

logger = logging.getLogger(__name__)

class CameraStrategy:
    """Base strategy class for camera-specific configurations and processing"""
    photogrammetry_settings = { "AeEnable": False, # Disable auto exposure
                                "NoiseReductionMode": 0, # Disable noise reduction
                                "AwbEnable": False # Disable automatic white balance
                               }

    def create_preview_config(self, picam: Picamera2, preview_resolution=None):
        raise NotImplementedError

    def create_photo_config(self, picam: Picamera2, photo_resolution=None):
        raise NotImplementedError

    def process_preview_frame(self, frame):
        return frame

    def process_photo_frame(self, frame):
        return frame

class IMX519Strategy(CameraStrategy):
    """Strategy class for camera-specific configurations and processing for the Arducam IMX519 camera with 16MP."""
    def create_preview_config(self, picam: Picamera2, preview_resolution=None):
        self.photogrammetry_settings.pop("ScalerCrop", None)
        if preview_resolution is None:
            return picam.create_preview_configuration(
                main={"size": (2328, 1748)},
                lores={"size": (640, 480), "format": "YUV420"},
                controls=self.photogrammetry_settings
            )
        return picam.create_preview_configuration(
            main={"size": preview_resolution},
            controls=self.photogrammetry_settings
        )

    def create_photo_config(self, picam: Picamera2, photo_resolution=None):
        if photo_resolution is None:
            return picam.create_still_configuration(buffer_count=1,
                                                    main={"size": (4656, 3496),  "format": "RGB888"},
                                                    controls=self.photogrammetry_settings
                                                    )
        return picam.create_still_configuration(buffer_count=1,
                                                main={"size": photo_resolution, "format": "RGB888"},
                                                controls=self.photogrammetry_settings
                                                )

    def process_preview_frame(self, frame):
        return frame[:, :, [2, 1, 0]]  # correct the color channels


class HawkeyeStrategy(CameraStrategy):
    """Strategy class for camera-specific configurations and processing for the Arducam Hawkeye camera with 64MP."""
    def create_preview_config(self, picam: Picamera2, preview_resolution=None):
        # adjust crop to match photo config
        # known limitation: fov is still not identical, preview image is slightly larger
        self.photogrammetry_settings["ScalerCrop"] = (624, 472, 8624, 6472)
        return picam.create_preview_configuration(
            transform=Transform(hflip=True, vflip=False),
            main={"size": (2328, 1748)},
            controls=self.photogrammetry_settings
            )

    def create_photo_config(self, picam: Picamera2, photo_resolution=None):
        if photo_resolution is None:
            # pop cropping dict key to use default cropping
            self.photogrammetry_settings.pop("ScalerCrop", None)
            return picam.create_still_configuration(
                buffer_count=1,
                transform=Transform(hflip=True, vflip=False),
                main={"size": (8000, 6000)},
                controls=self.photogrammetry_settings
                )
        return picam.create_still_configuration(buffer_count=1, main={"size": photo_resolution})

    def process_preview_frame(self, frame):
        return frame[:, :, [2, 1, 0]]  # correct the color channels

    def process_photo_frame(self, frame):
        return frame[:, :, [2, 1, 0]]  # correct the color channels


class Picamera2Controller(CameraController):
    _strategies = {
        "imx519": IMX519Strategy(),
        "arducam_64mp": HawkeyeStrategy()
    }

    def __init__(self, camera: Camera):
        super().__init__(camera)
        self._picam = Picamera2()
        self._strategy = self._strategies.get(self.camera.name, IMX519Strategy())

        self.control_mapping = {
            'shutter': 'ExposureTime',
            'saturation': 'Saturation',
            'contrast': 'Contrast',
            'gain': 'AnalogueGain',
            # 'awbg_red|blue': 'ColourGains', # ColourGains is a tuple of (red gain, blue gain)
            # 'crop_x|y': 'ScalerCrop' # ScalerCrop is a tuple of (x_offset, y_offset, width, height)
        }

        self._configure_resolutions()
        self._picam.configure(self.preview_config)
        self._picam.start()

        self._apply_settings_to_hardware(self.camera.settings)
        self._configure_focus(camera_mode="preview")


    def _apply_settings_to_hardware(self, settings: CameraSettings):
        """This method is call on every change of settings."""
        self._busy = True
        self._configure_focus()
        
        # apply all settings
        for setting, value in settings.__dict__.items():
            if setting in self.control_mapping:
                self._picam.set_controls({self.control_mapping[setting]: value})

        # handle ColourGains (AWB gains) separately
        red_gain = getattr(settings, 'awbg_red')
        blue_gain = getattr(settings, 'awbg_blue')
        if red_gain is not None and blue_gain is not None:
            self._picam.set_controls({'ColourGains': (red_gain, blue_gain)})
        self._busy = False
        logger.debug(f"Applied settings to hardware: {settings.model_dump_json()}")


    def _configure_resolutions(self):
        """Create preview and photo configurations."""
        self.preview_config = self._strategy.create_preview_config(self._picam, self.settings.preview_resolution)
        self.photo_config = self._strategy.create_photo_config(self._picam, self.settings.photo_resolution)


    def _configure_cropping(self):
        """Configure cropping of the image based on settings."""
        full_x, full_y = self._picam.camera_properties['PixelArraySize']
        # because of rotation, height is x and width is y
        crop_x = self.settings.crop_height / 100
        crop_y = self.settings.crop_width / 100

        x_start = int(full_x * crop_x / 2)
        x_end = int(full_x - x_start)
        y_start = int(full_y * crop_y / 2)
        y_end = int(full_y - y_start)

        return y_start, y_end, x_start, x_end


    def _configure_focus(self, camera_mode: str = None):
        """Configure focus based on settings.

        Args:
            camera_mode (str, optional): Whether to configure focus for "preview" or "photo" mode. Defaults to None.
        """
        if self.settings.AF:
            full_x, full_y = self._picam.camera_properties['PixelArraySize']

            if self.settings.AF_window is not None:
                x, y, w, h = self.settings.AF_window
                af_window = _transform_settings_to_camera_coordinates(setting_coordinates=(x, y),
                                                                      camera_resolution=(full_x, full_y),
                                                                      setting_size=(w, h))
            else:
                # Default the focus window to the central 1% of the image
                win_width = int(full_x * 0.1)
                win_height = int(full_y * 0.1)
                x_start = (full_x - win_width) // 2
                y_start = (full_y - win_height) // 2
                af_window = [(x_start, y_start, win_width, win_height)]

            self._picam.set_controls({
                    "AfMetering": controls.AfMeteringEnum.Windows,
                    "AfWindows": af_window
                })

            if camera_mode == "preview":
                # Configure continuous auto focus mode
                self._picam.set_controls({
                        "AfMode": controls.AfModeEnum.Continuous,
                        "AfSpeed": controls.AfSpeedEnum.Fast
                    })
            elif camera_mode == "photo":
                # Configure auto focus mode
                self._picam.set_controls({
                        "AfMode": controls.AfModeEnum.Auto,
                        "AfSpeed": controls.AfSpeedEnum.Fast
                    })
            logger.info(f"Auto focus enabled with AFWindow: {af_window}")

        else:
            # configure manual focus mode
            if self.settings.manual_focus is None:
                self.settings.manual_focus = 1.0
            self._picam.set_controls({"AfMode": controls.AfModeEnum.Manual, "LensPosition": self.settings.manual_focus})

            # Wait for focus with tolerance
            target_focus = float(self.settings.manual_focus)
            tolerance = 0.001 # 0.1% tolerance

            start = time.time()
            while abs(self._picam.capture_metadata()["LensPosition"] - target_focus) > tolerance:
                if time.time() - start > 5:
                    logger.warning(f"Warning: Focus timeout! Target: {target_focus}, Current: {self._picam.capture_metadata()['LensPosition']}")
                    break
                time.sleep(0.05) # wait for focus to be applied

            logger.info(f"Manual focus enabled, current Focus set to: {self._picam.capture_metadata()['LensPosition']}")


    def restart_camera(self):
        """Restart the camera."""
        self._picam.stop()
        self._configure_resolutions()
        self._picam.configure(self.preview_config)
        self._picam.start()
        logger.info(f"Picamera2 restarted.")


    def photo(self) -> IO[bytes]:
        """Capture a single photo.

        Capture a single photo using the current photo configuration.
        We capture a photo by switching to photo config and immediately returning to faster preview mode.
        This results in much faster autofocus adjustments after motor movements.
        The photo is processed according to the strategy and then converted to a JPEG image.

        Returns:
            IO[bytes]: A file-like object containing the JPEG image.
        """
        self._busy = True
        self._configure_focus(camera_mode="photo")
        if self.settings.AF:
            self._picam.autofocus_cycle()

        array = self._picam.switch_mode_and_capture_array(self.photo_config, "main")
        logger.debug(f"Captured photo array with metadata: {self._picam.capture_metadata()}")

        # reset focus to preview
        self._configure_focus(camera_mode="preview")

        if self.settings.crop_height > 0 or self.settings.crop_width > 0:
            y_start, y_end, x_start, x_end = self._configure_cropping()
            array = array[y_start:y_end, x_start:x_end]
            logger.debug(f"Cropped photo array.")

        array = cv2.rotate(array, cv2.ROTATE_90_COUNTERCLOCKWISE)
        array = self._strategy.process_photo_frame(array)

        _, jpeg = cv2.imencode('.jpg', array,
                               [int(cv2.IMWRITE_JPEG_QUALITY), self.settings.jpeg_quality])
        logger.debug(f"Converted photo array to JPEG with quality {self.settings.jpeg_quality}.")
        self._busy = False
        return jpeg.tobytes()


    def preview(self, mode="main") -> IO[bytes]:
        """Capture a preview.

        Capture a preview using the current preview configuration.
        The preview is processed according to the strategy and then converted to a JPEG image.

        Args:
            mode (str, optional): The mode to capture in either "main" or "lores". Defaults to "main".

        Returns:
            IO[bytes]: A file-like object containing the JPEG image.
        """
        #self._configure_focus(camera_mode="preview")

        frame = self._picam.capture_array(mode)

        if mode == "lores":
            frame = cv2.cvtColor(frame, cv2.COLOR_YUV420p2RGB)
            frame = apply_gamma_correction(frame, gamma=2.2)
        elif mode == "main":
            frame = cv2.resize(frame, (640,480))
            frame = self._strategy.process_preview_frame(frame)

        frame = cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
        _, jpeg = cv2.imencode('.jpg', frame)
        return jpeg.tobytes()


    def cleanup(self):
        """Clean up the camera resource."""
        self._picam.close()
        Picamera2Controller._picam = None


"""
Utility functions.
"""

def apply_gamma_correction(image, gamma=2.2):
    lookup_table = np.array([((i / 255.0) ** (1.0 / gamma)) * 255 for i in range(256)]).astype("uint8")
    return cv2.LUT(image, lookup_table)


def _transform_settings_to_camera_coordinates(setting_coordinates: tuple[int, int],
                                              camera_resolution: tuple[int, int],
                                              setting_size: tuple[int, int] = (0, 0),
                                              rotation_steps: int = 3) -> tuple[int, int, int, int]:
    """
    Transforms setting coordinates and adjusts them to camera coordinates, applying rotation
    steps as needed. Rotation is in 90 degree steps clockwise.

    Args:
        setting_coordinates (tuple[int, int]): X and Y coordinates of the setting
        camera_resolution (tuple[int, int]): X and Y resolution of the camera
        setting_size (tuple[int, int], optional): Width and height of the setting. Defaults to (0, 0).
        rotation_steps (int, optional): Number of 90 degree clockwise rotation steps. Defaults to 3.

    Returns:
        tuple[int, int, int, int]: Top-left X, Top-left Y, Width, Height of the setting in camera coordinates
    """
    setting_x, setting_y = setting_coordinates
    resolution_x, resolution_y = camera_resolution
    settings_width, setting_height = setting_size

    norm_rotation = rotation_steps % 4

    if norm_rotation == 0:  # 0 degrees rotation
        # User view is aligned with camera view
        cam_x, cam_y, cam_w, cam_h = setting_x, setting_y, settings_width, setting_height
    elif norm_rotation == 1:  # 90 degrees clockwise rotation
        # User's X-axis -> Camera's Y-axis (top to bottom)
        # User's Y-axis -> Camera's X-axis (right to left)
        cam_x = resolution_x - setting_y - setting_height
        cam_y = setting_x
        cam_w = setting_height
        cam_h = settings_width
    elif norm_rotation == 2:  # 180 degrees clockwise rotation
        # User view is upside down and mirrored relative to camera view
        cam_x = resolution_x - setting_x - settings_width
        cam_y = resolution_y - setting_y - setting_height
        cam_w = settings_width
        cam_h = setting_height
    elif norm_rotation == 3:  # 270 degrees clockwise rotation (or 90 degrees CCW)
        # User's X-axis -> Camera's Y-axis (bottom to top)
        # User's Y-axis -> Camera's X-axis (left to right)
        cam_x = setting_y
        cam_y = resolution_y - setting_x - settings_width
        cam_w = setting_height
        cam_h = settings_width
    else:
        # This case should not be reached due to modulo 4, but as a fallback:
        logger.error("Invalid norm rotation value")
        raise ValueError(f"Unexpected normalized rotation: {norm_rotation}")

    # Check if the window is outside the camera sensor boundaries
    if cam_x + cam_w > resolution_x or cam_y + cam_h > resolution_y:
        logger.error(
            f"Calculated AF window (x:{cam_x}, y:{cam_y}, w:{cam_w}, h:{cam_h}) "
            f"is outside the native camera sensor boundaries ({resolution_x}x{resolution_y}). "
            f"Original user settings: AF_window=({setting_x}, {setting_y}, {settings_width}, {setting_height}), ")
        raise ValueError(
            f"Calculated AF window (x:{cam_x}, y:{cam_y}, w:{cam_w}, h:{cam_h}) "
            f"is outside the native camera sensor boundaries ({resolution_x}x{resolution_y}). "
            f"Original user settings: AF_window=({setting_x}, {setting_y}, {settings_width}, {setting_height}), "
        )

    return cam_x, cam_y, cam_w, cam_h
