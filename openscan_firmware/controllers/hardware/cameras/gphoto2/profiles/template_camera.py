"""Template profile for adding a new gphoto2-compatible camera."""

from __future__ import annotations

from openscan_firmware.config.camera import CameraSettings

from ..profile import CameraIdentity
from ..profile_helpers import map_gain_to_iso_choice, restore_previous_config_value
from .generic import GenericGPhoto2Profile


class TemplateCameraProfile(GenericGPhoto2Profile):
    """Copy this class and replace values for your own camera model."""

    profile_id = "template_camera"
    register_in_registry = False

    # 1) Model markers: use lowercase fragments from `gphoto2 --auto-detect`.
    _MODEL_MARKERS = ("replace with model marker",)

    # 2) Key lists: inspect keys with `gphoto2 --list-config`.
    _CAPTURE_TARGET_KEYS = ["/main/settings/capturetarget", "capturetarget"]
    _SHUTTER_KEYS = ["/main/capturesettings/shutterspeed", "shutterspeed"]
    _JPEG_QUALITY_KEYS = ["/main/imgsettings/imagequality", "imagequality"]
    _ISO_KEYS = ["/main/imgsettings/iso", "iso"]
    _RAW_FORMAT_KEYS = ["/main/imgsettings/imageformat", "imageformat"]

    def matches(self, identity: CameraIdentity) -> bool:
        model = (identity.model or "").strip().lower()
        return any(marker in model for marker in self._MODEL_MARKERS)

    def apply_startup_config(self, session, settings: CameraSettings) -> None:
        # 3) Startup defaults: configure stable tethered behavior first.
        self._set_first(session, self._CAPTURE_TARGET_KEYS, "Memory card")
        self.apply_settings(session, settings)

    def apply_settings(self, session, settings: CameraSettings) -> None:
        # 4) Runtime settings: keep mapping logic explicit and readable.
        super().apply_settings(session, settings)
        iso_value = map_gain_to_iso_choice(settings.gain, [100, 200, 400, 800, 1600, 3200])
        if iso_value is not None:
            self._set_first(session, self._ISO_KEYS, iso_value)

    def supports_dng(self) -> bool:
        return True

    def capture_dng(self, session):
        # 5) RAW capture: only override if generic capture is not enough.
        previous = self._get_first_details(session, self._RAW_FORMAT_KEYS)
        previous_value = None if previous is None else previous.get("value")
        write_result = session.write_first_config(self._RAW_FORMAT_KEYS, "RAW")
        if not write_result.success:
            raise RuntimeError(
                f"Could not set RAW mode (attempted_keys={write_result.attempted_keys}, "
                f"error={write_result.error!r})."
            )
        try:
            return session.capture_image()
        finally:
            restore_previous_config_value(session, self._RAW_FORMAT_KEYS, previous_value)
