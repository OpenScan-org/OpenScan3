import abc
import io
from typing import Dict, List, IO, Optional, get_type_hints, Tuple

from app.models.camera import Camera, CameraType
from app.config.camera import CameraSettings
from app.models.project import Project

class SettingsObserver(abc.ABC):
    @abc.abstractmethod
    def update(self, setting: str, value: any):
        pass


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


class CameraController(abc.ABC):
    def __init__(self, camera: Camera):
        self.camera = camera
        self.camera.mode = None
        self._settings = camera.settings
        self._observers: List[SettingsObserver] = []

    @staticmethod
    def create(camera: Camera) -> 'CameraController':
        if camera.type == CameraType.GPHOTO2:
            from app.controllers.cameras.gphoto2 import Gphoto2Camera
            return Gphoto2Camera(camera)
        elif camera.type == CameraType.PICAMERA2:
            from app.controllers.cameras.picamera2 import Picamera2Controller
            return Picamera2Controller(camera)
        elif camera.type == CameraType.LINUXPY:
            from app.controllers.cameras.linuxpy import LINUXPYCamera
            return LINUXPYCamera(camera)
        else:
            raise ValueError(f"Couldn't find controller for {camera.type}")


    def add_observer(self, observer: SettingsObserver):
        self._observers.append(observer)

    def set_settings(self, settings: CameraSettings):
        settings_attributes = set(settings.__dict__.keys())
        self_attributes = set(self._settings.__dict__.keys())
        if settings_attributes != self_attributes:
            raise ValueError(f"settings should have the same attributes as self._settings. Got {settings_attributes}, expected {self_attributes}")
        self._settings = settings
        for observer in self._observers:
            for key, value in settings.__dict__.items():
                observer.update(key, value)
        return True


    def update_setting(self, setting: str, value):
        try:
            converted_value = convert_value(setting, value)
        except ValueError:
            return False
        setattr(self._settings, setting, converted_value)
        for observer in self._observers:
            observer.update(setting, converted_value)
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

class CameraControllerFactory:
    _controllers: Dict[str, CameraController] = {}

    @classmethod
    def get_controller(cls, camera: Camera) -> CameraController:
        if camera.name not in cls._controllers:
            cls._controllers[camera.name] = CameraController.create(camera)
        return cls._controllers[camera.name]

class ConfigObserver(SettingsObserver):
    def __init__(self, camera: Camera):
        self.camera = camera

    def update(self, setting: str, value: any):
        if hasattr(self.camera.settings, setting):
            setattr(self.camera.settings, setting, value)
            # Hier könnten Sie eine Methode zum Speichern der Konfiguration aufrufen
            # z.B. self.camera.settings.save()