"""Generic fallback profile for unknown GPhoto2 DSLR cameras."""

from __future__ import annotations

import logging
from fractions import Fraction

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
        session.set_first_config_value(self._CAPTURE_TARGET_KEYS, "Memory card")
        self.apply_settings(session, settings)

    def apply_settings(self, session, settings: CameraSettings) -> None:
        if settings.shutter is not None:
            shutter_str = self._select_best_shutter_choice(session, settings.shutter)
            applied = session.set_first_config_value(self._SHUTTER_KEYS, shutter_str)
            if not applied:
                logger.debug("No generic shutter config key found on camera.")

        if settings.jpeg_quality is not None and settings.jpeg_quality >= 85:
            session.set_first_config_value(self._JPEG_QUALITY_KEYS, "JPEG Fine")

    def _select_best_shutter_choice(self, session, shutter_ms: float) -> str:
        details = session.get_first_config_details(self._SHUTTER_KEYS)
        target_seconds = max(shutter_ms / 1000.0, 0.000125)
        if not details or not details.get("choices"):
            return _format_shutter_value_ms(shutter_ms)

        best = None
        best_err = float("inf")
        for choice in details["choices"]:
            parsed = _parse_shutter_choice_seconds(str(choice))
            if parsed is None:
                continue
            err = abs(parsed - target_seconds)
            if err < best_err:
                best_err = err
                best = str(choice)

        return best or _format_shutter_value_ms(shutter_ms)


def _parse_shutter_choice_seconds(value: str) -> float | None:
    v = value.strip().lower()
    if not v or v == "bulb":
        return None
    if "/" in v:
        try:
            return float(Fraction(v))
        except Exception:
            return None
    try:
        return float(v)
    except Exception:
        return None
