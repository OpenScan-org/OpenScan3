"""Generic fallback profile for unknown GPhoto2 DSLR cameras."""

from __future__ import annotations

import logging

from openscan_firmware.config.camera import CameraSettings

from ..profile import CameraIdentity, GPhoto2Profile

logger = logging.getLogger(__name__)


def _format_shutter_value_ms(shutter_ms: float) -> str:
    seconds = max(shutter_ms / 1000.0, 0.000125)
    if seconds >= 1.0:
        return f"{seconds:.1f}".rstrip("0").rstrip(".")
    reciprocal = round(1.0 / seconds)
    return f"1/{max(reciprocal, 1)}"


class GenericGPhoto2Profile(GPhoto2Profile):
    """Best-effort profile that targets common DSLR config keys."""

    profile_id = "generic"

    _CAPTURE_TARGET_KEYS = ["capturetarget", "capture", "recordingmedia"]
    _SHUTTER_KEYS = ["shutterspeed", "shutter_speed"]
    _JPEG_QUALITY_KEYS = ["imagequality", "imageformat", "imgquality"]

    def matches(self, identity: CameraIdentity) -> bool:
        return True

    def apply_startup_config(self, session, settings: CameraSettings) -> None:
        session.set_first_config_value(self._CAPTURE_TARGET_KEYS, "Memory card")
        self.apply_settings(session, settings)

    def apply_settings(self, session, settings: CameraSettings) -> None:
        if settings.shutter is not None:
            shutter_str = _format_shutter_value_ms(settings.shutter)
            applied = session.set_first_config_value(self._SHUTTER_KEYS, shutter_str)
            if not applied:
                logger.debug("No generic shutter config key found on camera.")

        if settings.jpeg_quality is not None and settings.jpeg_quality >= 85:
            session.set_first_config_value(self._JPEG_QUALITY_KEYS, "JPEG Fine")
