"""Canon EOS 700D specific GPhoto2 profile."""

from __future__ import annotations

from openscan_firmware.config.camera import CameraSettings

from ..profile import CameraIdentity
from .generic import GenericGPhoto2Profile


class CanonEOS700DProfile(GenericGPhoto2Profile):
    """Canon EOS 700D tuning on top of the generic DSLR behavior."""

    profile_id = "canon_eos_700d"

    _MODEL_MARKERS = ("canon eos 700d", "canon eos rebel t5i")
    _CAPTURE_TARGET_KEYS = ["capturetarget"]
    _SHUTTER_KEYS = ["shutterspeed"]
    _JPEG_QUALITY_KEYS = ["imageformat", "imagequality"]
    _DNG_KEYS = ["imageformat", "imagequality"]

    def matches(self, identity: CameraIdentity) -> bool:
        model = (identity.model or "").strip().lower()
        return any(marker in model for marker in self._MODEL_MARKERS)

    def apply_startup_config(self, session, settings: CameraSettings) -> None:
        # Canon DSLRs usually need explicit card target for stable capture.
        session.set_first_config_value(self._CAPTURE_TARGET_KEYS, "Memory card")
        self.apply_settings(session, settings)

    def supports_dng(self) -> bool:
        return True

    def capture_dng(self, session):
        # Try RAW+JPEG first, fall back to RAW only if the setting is unsupported.
        session.set_first_config_value(self._DNG_KEYS, "RAW + Large Fine JPEG")
        if not session.set_first_config_value(self._DNG_KEYS, "RAW"):
            pass
        return session.capture_image()
