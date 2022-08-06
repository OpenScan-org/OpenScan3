import io
from tempfile import TemporaryFile
from typing import IO
import gphoto2 as gp

from app.controllers.cameras.camera import CameraController
from app.models.camera import Camera


class Gphoto2Camera(CameraController):
    @classmethod
    def _get_camera(cls, camera: Camera) -> gp.Camera:
        if cls._camera is None:
            port_info_list = gp.PortInfoList()
            port_info_list.load()
            abilities_list = gp.CameraAbilitiesList()
            abilities_list.load()
            camera_list = abilities_list.detect(port_info_list)
            cls._camera = gp.Camera()
            idx = port_info_list.lookup_path(camera.path)
            cls._camera.set_port_info(port_info_list[idx])
            idx = abilities_list.lookup_model(camera_list[0][0])
            cls._camera.set_abilities(abilities_list[idx])
        return cls._camera

    @staticmethod
    def photo(camera: Camera) -> IO[bytes]:
        gp_camera = Gphoto2Camera._get_camera(camera)
        file_path = gp_camera.capture(gp.GP_CAPTURE_IMAGE)
        camera_file = gp_camera.file_get(
            file_path.folder, file_path.name, gp.GP_FILE_TYPE_NORMAL
        )
        file = TemporaryFile()
        file.write(camera_file.get_data_and_size())
        return file

    @staticmethod
    def preview(camera: Camera) -> IO[bytes]:
        gp_camera = Gphoto2Camera._get_camera(camera)
        camera_file = gp.gp_camera_capture_preview(gp_camera)[1]
        file = TemporaryFile()
        file.write(camera_file.get_data_and_size())
        return file
