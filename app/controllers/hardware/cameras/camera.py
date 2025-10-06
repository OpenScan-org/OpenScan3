"""
Camera Controller

This module provides a CameraController class for controlling cameras.
It implements the CameraController interface to manage the state of the camera.
Currently supporting only picamera2.
"""

import abc
import logging
from typing import IO


from app.models.camera import Camera, CameraType, PhotoData
from app.config.camera import CameraSettings
from app.controllers.hardware.interfaces import create_controller_registry, StatefulHardware
from app.controllers.settings import Settings

logger = logging.getLogger(__name__)

class CameraController(StatefulHardware):
    """
    Abstract Base Class for Camera Controller

    This class provides a generic interface for camera controllers.
    """
    def __init__(self, camera: Camera):
        self.camera = camera
        # Create settings with callback for hardware updates
        self.settings = Settings(camera.settings, on_change=self._on_settings_change)
        self._busy = False

    def get_status(self):
        """Get camera status"""
        return {"name": self.camera.name,
                "type": self.camera.type,
                "busy": self._busy,
                "settings": self.settings.model}

    def get_config(self) -> CameraSettings:
        return self.settings.model

    def _on_settings_change(self, settings: CameraSettings):
        self.camera.settings = settings
        self._apply_settings_to_hardware(settings)

    def _apply_settings_to_hardware(self, settings: CameraSettings):
        """
        This method is called automatically if settings change
        Has to be overwritten by camera controller subclasses.
        """
        raise NotImplementedError

    def is_busy(self):
        return self._busy

    def photo(self, image_format: str = "jpeg") -> PhotoData:
        """Capture a single photo with high resolution.

        Args:
            image_format (str, optional): Image format. Defaults to "jpeg".

        Returns:
            PhotoData: Captured photo data model.
        """
        handler = {
            "jpeg": self.capture_jpeg,
            "dng": self.capture_dng,
            "rgb_array": self.capture_rgb_array,
            "yuv_array": self.capture_yuv_array,
        }
        try:
            return handler[image_format]()
        except KeyError:
            raise ValueError(f"Unsupported image format: {image_format}")

    @abc.abstractmethod
    def preview(self) -> IO[bytes]:
        """Capture a faster and low resolution preview.

        Returns:
            IO[bytes]: Preview image data as jpeg bytes.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def capture_rgb_array(self) -> PhotoData:
        """Capture a numpy array to use for image analysis."""
        raise NotImplementedError

    @abc.abstractmethod
    def capture_yuv_array(self) -> PhotoData:
        """Capture a yuv array for image analysis."""
        raise NotImplementedError

    @abc.abstractmethod
    def capture_dng(self) -> PhotoData:
        """Capture a raw image and encode it to dng."""
        raise NotImplementedError

    @abc.abstractmethod
    def capture_jpeg(self) -> PhotoData:
        """Capture an image and encode it to jpeg."""
        raise NotImplementedError


def _create_camera_controller_instance(camera: Camera) -> 'CameraController':
    """Create a camera controller instance based on the camera type.
    Currently, supports only picamera2 properly.
    """
    if camera.type == CameraType.GPHOTO2:
        from .gphoto2 import Gphoto2Camera
        logger.debug("Creating Gphoto2 camera controller")
        return Gphoto2Camera(camera)
    elif camera.type == CameraType.PICAMERA2:
        logger.debug("Creating Picamera2 camera controller")
        from .picamera2 import Picamera2Controller
        return Picamera2Controller(camera)
    elif camera.type == CameraType.LINUXPY:
        logger.debug("Creating LinuxPy camera controller")
        from .linuxpy import LINUXPYCamera
        return LINUXPYCamera(camera)
    else:
        logger.error("Unknown camera type: {}".format(camera.type))
        raise ValueError(f"Couldn't find controller for {camera.type}")

create_camera_controller, get_camera_controller, remove_camera_controller, _camera_registry = create_controller_registry(_create_camera_controller_instance)


def get_all_camera_controllers():
    """Get all currently registered camera controllers"""
    return _camera_registry.copy()

def get_camera_controller_by_id(camera_id: int):
    controllers = list(get_all_camera_controllers().values())
    if len(controllers) < camera_id + 1:
        raise ValueError(f"Can't find camera controller with id {camera_id}")
    return controllers[camera_id]
