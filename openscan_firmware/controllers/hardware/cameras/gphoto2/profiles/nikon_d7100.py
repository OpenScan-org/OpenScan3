"""Nikon D7100 specific GPhoto2 profile."""

from __future__ import annotations

import logging
import time

from openscan_firmware.config.camera import CameraSettings

from ..profile import CameraIdentity
from .generic import GenericGPhoto2Profile

logger = logging.getLogger(__name__)


class NikonD7100Profile(GenericGPhoto2Profile):
    """Nikon D7100 tuning on top of generic DSLR behavior."""

    profile_id = "nikon_d7100"

    _MODEL_MARKERS = ("nikon dsc d7100", "nikon d7100")
    _CAPTURE_TARGET_KEYS = ["/main/settings/capturetarget", "capturetarget"]
    _RECORDING_MEDIA_KEYS = ["/main/settings/recordingmedia", "recordingmedia"]
    _APPLICATION_MODE_KEYS = ["/main/other/applicationmode", "applicationmode"]
    _SHUTTER_KEYS = ["/main/capturesettings/shutterspeed", "/main/settings/shutterspeed", "shutterspeed"]
    _JPEG_QUALITY_KEYS = ["/main/imgsettings/imagequality", "/main/imgsettings/imageformat", "imagequality", "imageformat"]
    _DNG_KEYS = ["/main/imgsettings/imagequality", "/main/imgsettings/imageformat", "imagequality", "imageformat"]
    _ISO_KEYS = ["/main/imgsettings/iso", "/main/capturesettings/iso", "iso"]

    def matches(self, identity: CameraIdentity) -> bool:
        model = (identity.model or "").strip().lower()
        return any(marker in model for marker in self._MODEL_MARKERS)

    def apply_startup_config(self, session, settings: CameraSettings) -> None:
        # Keep startup conservative and prefer the camera's normal card-backed routing.
        super().apply_startup_config(session, settings)
        session.set_first_config_value(self._CAPTURE_TARGET_KEYS, "Memory card")
        session.set_first_config_value(self._RECORDING_MEDIA_KEYS, "Card")

    def apply_settings(self, session, settings: CameraSettings) -> None:
        super().apply_settings(session, settings)
        iso_value = _map_gain_to_iso_choice(settings.gain)
        if iso_value is not None:
            applied = session.set_first_config_value(self._ISO_KEYS, iso_value)
            if not applied:
                logger.debug("ISO mapping unsupported on Nikon D7100 config tree.")

    def supports_dng(self) -> bool:
        return True

    def capture_dng(self, session):
        previous = session.get_first_config_details(self._DNG_KEYS)
        previous_value = None if previous is None else previous.get("value")

        raw_choice = _pick_nikon_raw_choice(session, self._DNG_KEYS)
        applied = session.set_first_config_value(self._DNG_KEYS, raw_choice)
        if not applied:
            raise RuntimeError(
                f"Could not set Nikon RAW mode (requested choice='{raw_choice}')."
            )

        try:
            # Nikon bodies can need a short settling delay after mode switch.
            time.sleep(0.12)
            capture_name = ""
            last_error: Exception | None = None

            for route in _capture_routes():
                _apply_capture_route(session, route)
                for attempt in range(1, 4):
                    try:
                        content, extra = session.capture_image()
                    except Exception as exc:
                        last_error = exc
                        if attempt < 3:
                            time.sleep(0.15 * attempt)
                            continue
                        break

                    capture_name = str(extra.get("capture_name", "")).lower()
                    if _is_raw_filename(capture_name):
                        return content, extra
                    if attempt < 3:
                        time.sleep(0.15 * attempt)

            if last_error is not None:
                raise RuntimeError(f"All Nikon RAW capture routes failed: {last_error}") from last_error

            raise RuntimeError(
                "Camera returned a non-RAW file while RAW was requested "
                f"(last capture_name='{capture_name or 'unknown'}')."
            )
        except Exception as exc:
            logger.exception("RAW capture failed in Nikon D7100 profile.")
            raise RuntimeError(f"RAW capture failed on Nikon D7100: {exc}") from exc
        finally:
            if previous_value:
                session.set_first_config_value(self._DNG_KEYS, previous_value)


def _pick_nikon_raw_choice(session, keys: list[str]) -> str:
    details = session.get_first_config_details(keys)
    if not details:
        return "RAW"
    choices = details.get("choices") or []
    for choice in choices:
        text = str(choice).strip().lower()
        if "raw" in text or "nef" in text:
            return str(choice)
    return "RAW"


def _capture_routes() -> list[dict[str, str]]:
    # Try the camera's current routing first, then explicit card-backed capture,
    # and only fall back to the older remote/RAM mode last.
    return [
        {},
        {
            "capturetarget": "Memory card",
            "recordingmedia": "Card",
            "applicationmode": "Application Mode 0",
        },
        {
            "capturetarget": "Internal RAM",
            "recordingmedia": "SDRAM",
            "applicationmode": "Application Mode 1",
        },
    ]


def _apply_capture_route(session, route: dict[str, str]) -> None:
    capturetarget = route.get("capturetarget")
    if capturetarget:
        session.set_first_config_value(NikonD7100Profile._CAPTURE_TARGET_KEYS, capturetarget)

    recordingmedia = route.get("recordingmedia")
    if recordingmedia:
        session.set_first_config_value(NikonD7100Profile._RECORDING_MEDIA_KEYS, recordingmedia)

    applicationmode = route.get("applicationmode")
    if applicationmode:
        session.set_first_config_value(NikonD7100Profile._APPLICATION_MODE_KEYS, applicationmode)


def _map_gain_to_iso_choice(gain: float | None) -> str | None:
    if gain is None:
        return None
    target = max(float(gain), 0.0) * 100.0
    iso_choices = [100, 200, 400, 800, 1600, 3200, 6400]
    nearest = min(iso_choices, key=lambda iso: abs(iso - target))
    return str(nearest)


def _is_raw_filename(name: str) -> bool:
    return name.lower().endswith((".nef", ".nrw", ".raw", ".dng", ".tif", ".tiff"))
