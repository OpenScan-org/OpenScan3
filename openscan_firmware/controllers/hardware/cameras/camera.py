"""
Camera Controller

This module provides a CameraController class for controlling cameras.
It implements the CameraController interface to manage the state of the camera.
Currently supporting only picamera2.
"""

import abc
import logging
from importlib import import_module
from typing import IO, Dict


from openscan_firmware.models.camera import Camera, CameraType, PhotoData
from openscan_firmware.config.camera import CameraSettings
from openscan_firmware.controllers.hardware.interfaces import create_controller_registry, StatefulHardware
from openscan_firmware.controllers.settings import Settings
from openscan_firmware.controllers.services.device_events import (
    notify_busy_change,
    schedule_device_status_broadcast,
)

logger = logging.getLogger(__name__)


_CAMERA_TYPE_MODULES = {
    CameraType.GPHOTO2: "gphoto2",
    CameraType.LINUXPY: "linuxpy.video.device",
    CameraType.PICAMERA2: "picamera2",
}
_camera_type_availability: Dict[CameraType, bool] = {}


def _is_module_available(module_path: str) -> bool:
    """Return True if the given module path can be imported."""
    try:
        import_module(module_path)
    except ModuleNotFoundError as exc:
        logger.info("Optional dependency '%s' not available: %s", module_path, exc)
        return False
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.error("Unexpected error while checking module '%s': %s", module_path, exc, exc_info=True)
        return False
    return True


def get_available_camera_types(force_refresh: bool = False) -> Dict[CameraType, bool]:
    """Return availability map for all known camera controller types."""
    if force_refresh or not _camera_type_availability:
        _camera_type_availability.clear()
        for camera_type, module_path in _CAMERA_TYPE_MODULES.items():
            _camera_type_availability[camera_type] = _is_module_available(module_path)
        # Camera types without explicit module dependencies default to True
        for camera_type in CameraType:
            _camera_type_availability.setdefault(camera_type, True)
    return _camera_type_availability.copy()


def is_camera_type_available(camera_type: CameraType) -> bool:
    """Convenience helper returning availability status for a single camera type."""
    return get_available_camera_types().get(camera_type, False)

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
        schedule_device_status_broadcast([f"cameras.{self.camera.name}.settings"])

    def _apply_settings_to_hardware(self, settings: CameraSettings):
        """
        This method is called automatically if settings change
        Has to be overwritten by camera controller subclasses.
        """
        raise NotImplementedError

    def is_busy(self):
        return self._busy

    def _set_busy(self, busy: bool) -> None:
        if self._busy == busy:
            return
        self._busy = busy
        notify_busy_change("cameras", self.camera.name)

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
        if not is_camera_type_available(CameraType.GPHOTO2):
            raise RuntimeError("GPhoto2 controller requested but the module is not available on this system.")
        from .gphoto2 import Gphoto2Camera
        logger.debug("Creating Gphoto2 camera controller")
        return Gphoto2Camera(camera)
    elif camera.type == CameraType.PICAMERA2:
        if not is_camera_type_available(CameraType.PICAMERA2):
            raise RuntimeError("Picamera2 controller requested but the module is not available on this system.")
        logger.debug("Creating Picamera2 camera controller")
        from .picamera2 import Picamera2Controller
        return Picamera2Controller(camera)
    elif camera.type == CameraType.LINUXPY:
        if not is_camera_type_available(CameraType.LINUXPY):
            raise RuntimeError("LinuxPy controller requested but the module is not available on this system.")
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
