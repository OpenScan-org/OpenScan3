import abc
from typing import Dict, IO, Optional, Type

from app.models.camera import Camera, CameraType
from app.config.camera import CameraSettings
from app.controllers.hardware.interfaces import create_controller_registry
from app.controllers.settings import Settings

class CameraController(abc.ABC):
    def __init__(self, camera: Camera):
        self.camera = camera
        # Create settings with callback for hardware updates
        self.settings = Settings(camera.settings, on_change=self._apply_settings_to_hardware)
        self._busy = False

    def get_status(self):
        """Get camera status"""
        return {"name": self.camera.name,
                "type": self.camera.type,
                "busy": self._busy,
                "settings": self.settings.model}


    @abc.abstractmethod
    def _apply_settings_to_hardware(self, settings: CameraSettings):
        """
        This method is called automatically if settings change
        Has to be overwritten by camera controller subclasses.
        """
        pass

    @staticmethod
    @abc.abstractmethod
    def photo(camera: Camera) -> IO[bytes]:
        raise NotImplementedError

    @staticmethod
    @abc.abstractmethod
    def preview(camera: Camera) -> IO[bytes]:
        raise NotImplementedError

def _create_camera_controller_instance(camera: Camera) -> 'CameraController':
    if camera.type == CameraType.GPHOTO2:
        from .gphoto2 import Gphoto2Camera
        return Gphoto2Camera(camera)
    elif camera.type == CameraType.PICAMERA2:
        from .picamera2 import Picamera2Controller
        return Picamera2Controller(camera)
    elif camera.type == CameraType.LINUXPY:
        from .linuxpy import LINUXPYCamera
        return LINUXPYCamera(camera)
    else:
        raise ValueError(f"Couldn't find controller for {camera.type}")

create_camera_controller, get_camera_controller, remove_camera_controller, _camera_registry = create_controller_registry(_create_camera_controller_instance)


def get_all_camera_controllers():
    """Get all currently registered light controllers"""
    return _camera_registry.copy()

def get_camera_controller_by_id(camera_id: int):
    controllers = list(get_all_camera_controllers().values())
    if len(controllers) < camera_id + 1:
        raise ValueError(f"Can't find camera controller with id {camera_id}")
    return controllers[camera_id]
