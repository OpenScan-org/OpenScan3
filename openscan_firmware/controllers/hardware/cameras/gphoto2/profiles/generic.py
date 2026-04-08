"""Generic fallback profile for unknown GPhoto2 DSLR cameras."""

from __future__ import annotations

import logging

from openscan_firmware.config.camera import CameraSettings

from ..profile import CameraIdentity, GPhoto2Profile
from ..profile_helpers import (
    format_shutter_value_ms,
    parse_shutter_choice_seconds,
    select_best_shutter_choice,
)

logger = logging.getLogger(__name__)


def _format_shutter_value_ms(shutter_ms: float) -> str:
    return format_shutter_value_ms(shutter_ms)


class GenericGPhoto2Profile(GPhoto2Profile):
    """Best-effort profile that targets common DSLR config keys."""

    profile_id = "generic"

    _CAPTURE_TARGET_KEYS = [
        "/main/settings/capturetarget",
        "capturetarget",
        "capture",
        "recordingmedia",
    ]
    _SHUTTER_KEYS = [
        "/main/capturesettings/shutterspeed",
        "/main/settings/shutterspeed",
        "shutterspeed",
        "shutter_speed",
    ]
    _JPEG_QUALITY_KEYS = ["/main/imgsettings/imagequality", "imagequality", "imageformat", "imgquality"]

    def matches(self, identity: CameraIdentity) -> bool:
        return True

    def apply_startup_config(self, session, settings: CameraSettings) -> None:
        self._set_first(session, self._CAPTURE_TARGET_KEYS, "Memory card")
        self.apply_settings(session, settings)

    def apply_settings(self, session, settings: CameraSettings) -> None:
        if settings.shutter is not None:
            shutter_str = self._select_best_shutter_choice(session, settings.shutter)
            applied = self._set_first(session, self._SHUTTER_KEYS, shutter_str)
            if not applied:
                logger.debug("No generic shutter config key found on camera.")

        if settings.jpeg_quality is not None and settings.jpeg_quality >= 85:
            self._set_first(session, self._JPEG_QUALITY_KEYS, "JPEG Fine")

    def _select_best_shutter_choice(self, session, shutter_ms: float) -> str:
        details = self._get_first_details(session, self._SHUTTER_KEYS)
        choices = [] if details is None else list(details.get("choices") or [])
        return select_best_shutter_choice(shutter_ms, choices)


def _parse_shutter_choice_seconds(value: str) -> float | None:
    return parse_shutter_choice_seconds(value)
