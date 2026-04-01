"""Low-level session wrapper around python-gphoto2."""

from __future__ import annotations

import logging
from typing import Any

import gphoto2 as gp

from .profile import CameraIdentity

logger = logging.getLogger(__name__)


class GPhoto2Session:
    """Manage a gphoto2 camera session for one physical device."""

    def __init__(self, camera_path: str, model_hint: str | None = None):
        self._camera_path = camera_path
        self._model_hint = model_hint
        self._camera: Any | None = None
        self._identity = CameraIdentity(model=model_hint, port=camera_path)

    @property
    def identity(self) -> CameraIdentity:
        return self._identity

    def ensure_connected(self) -> Any:
        if self._camera is not None:
            return self._camera

        port_info_list = gp.PortInfoList()
        port_info_list.load()
        abilities_list = gp.CameraAbilitiesList()
        abilities_list.load()

        detected_model = self._model_hint
        try:
            camera_list = abilities_list.detect(port_info_list)
            for idx in range(camera_list.count()):
                model_name = camera_list.get_name(idx)
                detected_path = camera_list.get_value(idx)
                if detected_path == self._camera_path:
                    detected_model = model_name
                    break
        except Exception:
            logger.debug("GPhoto2 autodetect lookup failed.", exc_info=True)

        camera = gp.Camera()
        if self._camera_path:
            port_idx = port_info_list.lookup_path(self._camera_path)
            if port_idx >= 0:
                camera.set_port_info(port_info_list[port_idx])

        if detected_model:
            try:
                abilities_idx = abilities_list.lookup_model(detected_model)
                if abilities_idx >= 0:
                    camera.set_abilities(abilities_list[abilities_idx])
            except Exception:
                logger.debug("Failed setting camera abilities for '%s'.", detected_model, exc_info=True)

        camera.init()
        self._camera = camera
        self._identity = CameraIdentity(model=detected_model or self._model_hint, port=self._camera_path)
        return camera

    def close(self) -> None:
        if self._camera is None:
            return
        try:
            self._camera.exit()
        except Exception:
            logger.debug("Failed to close gphoto2 camera session cleanly.", exc_info=True)
        finally:
            self._camera = None

    def capture_preview(self) -> bytes:
        camera = self.ensure_connected()
        camera_file = gp.gp_camera_capture_preview(camera)[1]
        return bytes(camera_file.get_data_and_size())

    def capture_image(self, gp_file_type: int = gp.GP_FILE_TYPE_NORMAL) -> tuple[bytes, dict[str, Any]]:
        camera = self.ensure_connected()
        file_path = camera.capture(gp.GP_CAPTURE_IMAGE)
        camera_file = camera.file_get(file_path.folder, file_path.name, gp_file_type)
        payload = bytes(camera_file.get_data_and_size())
        metadata = {
            "capture_folder": file_path.folder,
            "capture_name": file_path.name,
            "gp_file_type": gp_file_type,
        }
        return payload, metadata

    def set_config_value(self, key: str, value: Any) -> bool:
        camera = self.ensure_connected()
        config = camera.get_config()
        child = self._find_widget(config, key)
        if child is None:
            return False
        child.set_value(value)
        camera.set_config(config)
        return True

    def set_first_config_value(self, keys: list[str], value: Any) -> bool:
        for key in keys:
            try:
                if self.set_config_value(key, value):
                    return True
            except Exception:
                logger.debug("Setting config '%s' failed.", key, exc_info=True)
        return False

    @staticmethod
    def _find_widget(config_root: Any, key: str) -> Any | None:
        if hasattr(config_root, "get_child_by_name"):
            try:
                return config_root.get_child_by_name(key)
            except Exception:
                return None
        return None
