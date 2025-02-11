import abc
from typing import Dict, IO, Optional, get_type_hints, Tuple

from app.models.camera import Camera, CameraType
from app.config.camera import CameraSettings
from app.controllers.hardware.interfaces import ControllerFactory

class CameraController(abc.ABC):
    def __init__(self, camera: Camera):
        self.camera = camera
        self._settings = camera.settings

    @staticmethod
    def create(camera: Camera) -> 'CameraController':
        if camera.type == CameraType.GPHOTO2:
            from controllers.hardware.cameras.gphoto2 import Gphoto2Camera
            return Gphoto2Camera(camera)
        elif camera.type == CameraType.PICAMERA2:
            from controllers.hardware.cameras.picamera2 import Picamera2Controller
            return Picamera2Controller(camera)
        elif camera.type == CameraType.LINUXPY:
            from controllers.hardware.cameras.linuxpy import LINUXPYCamera
            return LINUXPYCamera(camera)
        else:
            raise ValueError(f"Couldn't find controller for {camera.type}")

    def set_settings(self, settings: CameraSettings):
        self._settings = settings
        self._apply_settings_to_hardware()
        self._save_settings_to_config()
        return True

    def update_setting(self, setting: str, value):
        converted_value = convert_value(setting, value)
        setattr(self._settings, setting, converted_value)
        self._apply_single_setting(setting, converted_value)
        self.camera.save_settings()
        return True

    def get_setting(self, setting: str) -> any:
        if not hasattr(self._settings, setting):
            raise ValueError(f"Unknown setting: {setting}")
        return getattr(self._settings, setting)

    def get_all_settings(self) -> Dict[str, any]:
        return self._settings.__dict__

    @staticmethod
    @abc.abstractmethod
    def photo(camera: Camera) -> IO[bytes]:
        raise NotImplementedError

    @staticmethod
    @abc.abstractmethod
    def preview(camera: Camera) -> IO[bytes]:
        raise NotImplementedError

    def _apply_settings_to_hardware(self):
        # Implementieren Sie hier die Anwendung der Einstellungen auf die Hardware
        pass

    def _apply_single_setting(self, setting: str, value: any):
        # Implementieren Sie hier die Anwendung einer einzelnen Einstellung auf die Hardware
        pass

    def _save_settings_to_config(self):
        # Implementieren Sie hier das Speichern der Einstellungen in der Konfiguration
        pass


class CameraControllerFactory(ControllerFactory[CameraController, Camera]):
    _controller_class = CameraController
    
    @classmethod
    def _create_controller(cls, model: Camera) -> CameraController:
        return CameraController.create(model)  # Spezielle Erstellung für Kameras



def convert_value(setting: str, value: str):
    type_hints = get_type_hints(CameraSettings)
    if setting not in type_hints:
        raise ValueError(f"Unknown setting: {setting}")

    target_type = type_hints[setting]

    if target_type == Optional[int]:
        return int(value)
    elif target_type == Optional[float]:
        return float(value)
    elif target_type == Optional[bool]:
        return value.lower() in {"true", "1", "yes"}
    elif target_type == Optional[Tuple[int, int]]:
        return tuple(map(int, value.split(",")))

    return value  # in case of str value