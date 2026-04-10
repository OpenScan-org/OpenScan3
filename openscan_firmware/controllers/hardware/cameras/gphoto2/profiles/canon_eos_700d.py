"""Canon EOS 700D specific GPhoto2 profile."""

from __future__ import annotations

import logging

from openscan_firmware.config.camera import CameraSettings

from ..profile import CameraIdentity
from ..profile_helpers import is_raw_filename, map_gain_to_iso_choice, restore_previous_config_value
from .generic import GenericGPhoto2Profile

logger = logging.getLogger(__name__)


class CanonEOS700DProfile(GenericGPhoto2Profile):
    """Canon EOS 700D tuning on top of the generic DSLR behavior."""

    profile_id = "canon_eos_700d"

    _MODEL_MARKERS = ("canon eos 700d", "canon eos rebel t5i")
    _CAPTURE_TARGET_KEYS = ["/main/settings/capturetarget", "capturetarget"]
    _SHUTTER_KEYS = ["/main/capturesettings/shutterspeed", "shutterspeed"]
    _JPEG_QUALITY_KEYS: list[str] = []
    _DNG_KEYS = ["/main/imgsettings/imageformat", "imageformat"]
    _EXPOSURE_MODE_KEYS = ["/main/capturesettings/autoexposuremode", "autoexposuremode"]
    _FOCUS_MODE_KEYS = ["/main/capturesettings/focusmode", "focusmode"]
    _ISO_KEYS = ["/main/imgsettings/iso", "iso"]

    def matches(self, identity: CameraIdentity) -> bool:
        model = (identity.model or "").strip().lower()
        return any(marker in model for marker in self._MODEL_MARKERS)

    def apply_startup_config(self, session, settings: CameraSettings) -> None:
        # For tethered capture on EOS 700D we prefer Internal RAM.
        self._set_first(session, self._CAPTURE_TARGET_KEYS, "Internal RAM")
        self._set_first(session, self._EXPOSURE_MODE_KEYS, "Manual")
        self._set_first(session, self._FOCUS_MODE_KEYS, "One Shot")
        self.apply_settings(session, settings)

    def apply_settings(self, session, settings: CameraSettings) -> None:
        super().apply_settings(session, settings)

        iso_value = _map_gain_to_iso_choice(settings.gain)
        if iso_value is not None:
            applied = self._set_first(session, self._ISO_KEYS, iso_value)
            if not applied:
                logger.debug("ISO mapping unsupported on this EOS 700D config tree.")

    def supports_dng(self) -> bool:
        return True

    def capture_dng(self, session):
        previous = self._get_first_details(session, self._DNG_KEYS)
        previous_value = None if previous is None else previous.get("value")
        write_result = session.write_first_config(self._DNG_KEYS, "RAW")
        if not write_result.success:
            raise RuntimeError(
                "Could not set Canon RAW mode "
                f"(requested='RAW', attempted_keys={write_result.attempted_keys}, "
                f"error={write_result.error!r})."
            )
        try:
            # EOS 700D is more stable with normal file download after forcing
            # imageformat=RAW than with GP_FILE_TYPE_RAW.
            content, extra = session.capture_image()
            capture_name = str(extra.get("capture_name", "")).lower()
            if _is_raw_filename(capture_name):
                return content, extra
            raise RuntimeError(
                "Camera returned a non-RAW file while RAW was requested "
                f"(capture_name='{capture_name or 'unknown'}')."
            )
        except Exception as exc:
            raise RuntimeError(f"RAW capture failed on Canon EOS 700D: {exc}") from exc
        finally:
            restore_previous_config_value(session, self._DNG_KEYS, previous_value)


def _map_gain_to_iso_choice(gain: float | None) -> str | None:
    # CameraSettings.gain is generic analogue gain; for DSLR map to nearest ISO stop.
    return map_gain_to_iso_choice(gain, [100, 200, 400, 800, 1600, 3200, 6400, 12800])


def _is_raw_filename(name: str) -> bool:
    return is_raw_filename(name, (".cr2", ".cr3", ".crw", ".raw", ".dng"))
