import abc
from typing import Dict, IO, Optional

from app.models.camera import Camera, CameraType
from app.config.camera import CameraSettings
from app.controllers.hardware.interfaces import ControllerFactory
from app.controllers.settings import SettingsManager

class CameraController(abc.ABC):
    def __init__(self, camera: Camera):
        self.camera = camera
        # Create SettingsManager with callback for updating hardware settings
        self.settings_manager = SettingsManager(
            camera,
            on_settings_changed=self._apply_settings_to_hardware
        )
        self._busy = False

    @staticmethod
    def create(camera: Camera) -> 'CameraController':
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

    def get_setting(self, setting: str) -> any:
        """Get a camera setting"""
        return self.settings_manager.get_setting(setting)

    def set_setting(self, setting: str, value: any) -> bool:
        """Update a single camera setting"""
        try:
            if value is not None:
                self.settings_manager.set_setting(setting, value)
            return True
        except ValueError as e:
            print(f"Error updating setting {setting}: {e}")
            return False

    def get_all_settings(self) -> CameraSettings:
        """Get all camera settings"""
        return self.settings_manager.get_all_settings()

    def replace_settings(self, settings: CameraSettings) -> bool:
        """Replace all camera settings at once"""
        try:
            self.settings_manager.replace_settings(settings)
            return True
        except ValueError as e:
            print(f"Error replacing settings: {e}")

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


class CameraControllerFactory(ControllerFactory[CameraController, Camera]):
    _controller_class = CameraController
    
    @classmethod
    def _create_controller(cls, model: Camera) -> CameraController:
        return CameraController.create(model)