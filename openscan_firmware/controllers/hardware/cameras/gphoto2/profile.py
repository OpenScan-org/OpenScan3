"""Profile abstraction for camera model-specific GPhoto2 behavior."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from openscan_firmware.config.camera import CameraSettings

from .profile_helpers import select_best_shutter_choice

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CameraIdentity:
    model: str | None
    port: str | None


class GPhoto2Profile:
    """Base profile contract for camera model-specific GPhoto2 behavior."""

    profile_id = "generic"
    register_in_registry = True

    def matches(self, identity: CameraIdentity) -> bool:
        return True

    def apply_startup_config(self, session: Any, settings: CameraSettings) -> None:
        """Apply one-time defaults when the controller starts."""

    def apply_settings(self, session: Any, settings: CameraSettings) -> None:
        """Apply runtime settings updates."""

    def supports_dng(self) -> bool:
        return False

    def capture_dng(self, session: Any) -> tuple[bytes, dict[str, Any]]:
        raise ValueError(f"Profile '{self.profile_id}' does not support DNG capture.")

    def build_metadata(
        self,
        identity: CameraIdentity,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "driver": "gphoto2",
            "profile": self.profile_id,
            "model": identity.model,
            "port": identity.port,
        }
        if extra:
            metadata.update(extra)
        return metadata

    # Shared helper methods for profile implementations.
    def _set_first(self, session: Any, keys: list[str], value: Any) -> bool:
        return session.write_first_config(keys, value).success

    def _get_first_details(self, session: Any, keys: list[str]) -> dict[str, Any] | None:
        result = session.read_first_config(keys)
        return result.details if result.success else None

    def _pick_best_shutter(self, session: Any, keys: list[str], shutter_ms: float) -> str:
        details = self._get_first_details(session, keys)
        choices = [] if details is None else list(details.get("choices") or [])
        return select_best_shutter_choice(shutter_ms=shutter_ms, available_choices=choices)
