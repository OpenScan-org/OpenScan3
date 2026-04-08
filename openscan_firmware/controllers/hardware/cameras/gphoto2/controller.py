"""High-level GPhoto2 camera controller."""

from __future__ import annotations

import io
import logging
from typing import IO

import cv2  # type: ignore[import]
import numpy as np
import piexif  # type: ignore[import]

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

    def get_diagnostics(self) -> dict:
        """Return diagnostics gathered from the active gphoto2 controller session."""
        relevant_keys = [
            "/main/settings/capturetarget",
            "/main/settings/capture",
            "/main/settings/recordingmedia",
            "/main/settings/remotemode",
            "/main/capturesettings/shutterspeed",
            "/main/capturesettings/aperture",
            "/main/capturesettings/autoexposuremode",
            "/main/capturesettings/focusmode",
            "/main/imgsettings/imageformat",
            "/main/imgsettings/imagequality",
            "/main/imgsettings/iso",
            "/main/status/liveviewstatus",
            "/main/status/liveviewselector",
            "/main/other/applicationmode",
            "capturetarget",
            "capture",
            "recordingmedia",
            "remotemode",
            "shutterspeed",
            "aperture",
            "autoexposuremode",
            "focusmode",
            "imageformat",
            "imagequality",
            "iso",
            "liveviewstatus",
            "liveviewselector",
            "applicationmode",
        ]
        with self._hw_lock:
            camera = self._session.ensure_connected()
            summary = None
            about = None
            groups: list[str] = []
            relevant: list[dict] = []

            try:
                summary = str(getattr(camera.get_summary(), "text", "")).strip() or None
            except Exception:
                summary = None
            try:
                about = str(getattr(camera.get_about(), "text", "")).strip() or None
            except Exception:
                about = None

            try:
                config = camera.get_config()
                child_count = config.count_children()
                for child_idx in range(child_count):
                    child = config.get_child(child_idx)
                    groups.append(f"{child.get_name()}: {child.get_label()}")
            except Exception:
                groups = []

            seen_paths: set[str] = set()
            for key in relevant_keys:
                read_result = self._session.read_config(key)
                if not read_result.success or read_result.details is None:
                    continue
                details = read_result.details
                key_path = str(details.get("key", key))
                if key_path in seen_paths:
                    continue
                seen_paths.add(key_path)
                relevant.append(details)

            identity = self._session.identity
            return {
                "model": identity.model or self.camera.name,
                "path": identity.port or self.camera.path,
                "summary": summary,
                "about": about,
                "config_groups": groups,
                "relevant_config": relevant,
                "profile": self._profile.profile_id,
                "in_use_by_openscan": True,
                "error": None,
            }

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
            content = self._embed_orientation_flag(content)
            return self._create_photo_data(io.BytesIO(content), "jpeg", extra)
        finally:
            self._set_busy(False)

    def capture_dng(self) -> PhotoData:
        self._set_busy(True)
        try:
            content, extra = self._profile.capture_dng(self._session)
            return self._create_photo_data(io.BytesIO(content), "raw", extra)
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

    def _embed_orientation_flag(self, jpeg_bytes: bytes) -> bytes:
        orientation = self.settings.orientation_flag
        if orientation is None:
            return jpeg_bytes
        try:
            flag = int(orientation)
            exif_bytes = piexif.dump({"0th": {piexif.ImageIFD.Orientation: flag}})
            return piexif.insert(exif_bytes, jpeg_bytes)
        except Exception as exc:
            logger.warning(
                "Failed to embed orientation flag (%s) into gphoto2 JPEG: %s",
                orientation,
                exc,
            )
            return jpeg_bytes


# Backward-compatible class name used in existing imports.
Gphoto2Camera = GPhoto2Controller
