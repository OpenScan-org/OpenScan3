"""Nikon D7100 specific GPhoto2 profile."""

from __future__ import annotations

import logging
import time

from openscan_firmware.config.camera import CameraSettings

from ..profile import CameraIdentity
from ..profile_helpers import (
    capture_with_route_fallbacks,
    is_raw_filename,
    map_gain_to_iso_choice,
    pick_raw_choice_from_details,
    restore_previous_config_value,
)
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
        self._set_first(session, self._CAPTURE_TARGET_KEYS, "Memory card")
        self._set_first(session, self._RECORDING_MEDIA_KEYS, "Card")

    def apply_settings(self, session, settings: CameraSettings) -> None:
        super().apply_settings(session, settings)
        iso_value = _map_gain_to_iso_choice(settings.gain)
        if iso_value is not None:
            applied = self._set_first(session, self._ISO_KEYS, iso_value)
            if not applied:
                logger.debug("ISO mapping unsupported on Nikon D7100 config tree.")

    def supports_dng(self) -> bool:
        return True

    def capture_dng(self, session):
        previous = self._get_first_details(session, self._DNG_KEYS)
        previous_value = None if previous is None else previous.get("value")

        raw_choice = _pick_nikon_raw_choice(previous)
        write_result = session.write_first_config(self._DNG_KEYS, raw_choice)
        if not write_result.success:
            raise RuntimeError(
                "Could not set Nikon RAW mode "
                f"(requested choice='{raw_choice}', attempted_keys={write_result.attempted_keys}, "
                f"error={write_result.error!r})."
            )

        try:
            # Nikon bodies can need a short settling delay after mode switch.
            time.sleep(0.12)
            content, extra, diagnostics = capture_with_route_fallbacks(
                session=session,
                routes=_capture_routes(),
                capture_route_applier=_apply_capture_route,
                raw_filename_checker=_is_raw_filename,
            )
            extra.update(diagnostics)
            return content, extra
        except Exception as exc:
            logger.exception("RAW capture failed in Nikon D7100 profile.")
            raise RuntimeError(f"RAW capture failed on Nikon D7100: {exc}") from exc
        finally:
            restore_previous_config_value(session, self._DNG_KEYS, previous_value)


def _pick_nikon_raw_choice(details: dict | None) -> str:
    return pick_raw_choice_from_details(details, markers=("raw", "nef"))


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
        session.write_first_config(NikonD7100Profile._CAPTURE_TARGET_KEYS, capturetarget)

    recordingmedia = route.get("recordingmedia")
    if recordingmedia:
        session.write_first_config(NikonD7100Profile._RECORDING_MEDIA_KEYS, recordingmedia)

    applicationmode = route.get("applicationmode")
    if applicationmode:
        session.write_first_config(NikonD7100Profile._APPLICATION_MODE_KEYS, applicationmode)


def _map_gain_to_iso_choice(gain: float | None) -> str | None:
    return map_gain_to_iso_choice(gain, [100, 200, 400, 800, 1600, 3200, 6400])


def _is_raw_filename(name: str) -> bool:
    return is_raw_filename(name, (".nef", ".nrw", ".raw", ".dng", ".tif", ".tiff"))
