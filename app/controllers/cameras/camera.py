import abc
import io
from typing import IO

from app.models.camera import Camera
from app.models.project import Project


class CameraController(abc.ABC):

    @staticmethod
    @abc.abstractmethod
    def photo(camera: Camera) -> IO[bytes]:
        raise NotImplementedError

    @staticmethod
    @abc.abstractmethod
    def preview(camera: Camera) -> IO[bytes]:
        raise NotImplementedError
