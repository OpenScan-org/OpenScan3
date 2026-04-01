"""High-level GPhoto2 camera controller."""

from __future__ import annotations

import io
import logging
from typing import IO

import cv2  # type: ignore[import]
import numpy as np

from openscan_firmware.config.camera import CameraSettings
from openscan_firmware.models.camera import Camera, CameraMetadata, PhotoData

from ..camera import CameraController
from .profile_registry import get_profile_for_identity
from .session import GPhoto2Session

logger = logging.getLogger(__name__)


class GPhoto2Controller(CameraController):
    """CameraController implementation for USB DSLR cameras via gphoto2."""

    def __init__(self, camera: Camera):
        if camera.settings is None:
            camera.settings = CameraSettings()
        super().__init__(camera)
        self._session = GPhoto2Session(camera_path=camera.path, model_hint=camera.name)
        self._session.ensure_connected()
        self._profile = get_profile_for_identity(self._session.identity)
        logger.info(
            "Initialized gphoto2 controller for '%s' with profile '%s'.",
            camera.name,
            self._profile.profile_id,
        )
        self._profile.apply_startup_config(self._session, self.settings.model)

    def cleanup(self):
        self._session.close()

    def _apply_settings_to_hardware(self, settings: CameraSettings):
        self._set_busy(True)
        try:
            self._profile.apply_settings(self._session, settings)
        finally:
            self._set_busy(False)

    def preview(self) -> IO[bytes]:
        self._set_busy(True)
        try:
            return self._session.capture_preview()
        finally:
            self._set_busy(False)

    def capture_jpeg(self) -> PhotoData:
        self._set_busy(True)
        try:
            content, extra = self._session.capture_image()
            return self._create_photo_data(io.BytesIO(content), "jpeg", extra)
        finally:
            self._set_busy(False)

    def capture_dng(self) -> PhotoData:
        self._set_busy(True)
        try:
            content, extra = self._profile.capture_dng(self._session)
            return self._create_photo_data(io.BytesIO(content), "dng", extra)
        finally:
            self._set_busy(False)

    def capture_rgb_array(self) -> PhotoData:
        rgb_array = self._capture_rgb_array()
        return self._create_photo_data(rgb_array, "rgb_array")

    def capture_yuv_array(self) -> PhotoData:
        rgb_array = self._capture_rgb_array()
        yuv_array = cv2.cvtColor(rgb_array, cv2.COLOR_RGB2YUV)
        return self._create_photo_data(yuv_array, "yuv_array")

    def _capture_rgb_array(self) -> np.ndarray:
        self._set_busy(True)
        try:
            content, _ = self._session.capture_image()
            np_buffer = np.frombuffer(content, dtype=np.uint8)
            bgr_image = cv2.imdecode(np_buffer, cv2.IMREAD_COLOR)
            if bgr_image is None:
                raise RuntimeError("Failed to decode JPEG payload from gphoto2 capture.")
            return cv2.cvtColor(bgr_image, cv2.COLOR_BGR2RGB)
        finally:
            self._set_busy(False)

    def _create_photo_data(self, data, data_format: str, extra: dict | None = None) -> PhotoData:
        metadata = CameraMetadata(
            camera_name=self.camera.name,
            camera_settings=self.settings.model,
            raw_metadata=self._profile.build_metadata(self._session.identity, extra=extra),
        )
        return PhotoData(
            data=data,
            format=data_format,
            camera_metadata=metadata,
        )


# Backward-compatible class name used in existing imports.
Gphoto2Camera = GPhoto2Controller
