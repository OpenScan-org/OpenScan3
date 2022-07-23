import abc
import io

from app.models.camera import Camera
from app.models.project import Project


class CameraController(abc.ABC):
    @staticmethod
    @abc.abstractmethod
    def photo(camera: Camera) -> io.BytesIO:
        raise NotImplementedError

    @staticmethod
    @abc.abstractmethod
    def preview(camera: Camera) -> io.BytesIO:
        raise NotImplementedError

    @staticmethod
    def save_photo(camera: Camera, project: Project) -> bool:
        photo = CameraController.photo(camera)
        return False
