"""Profile abstraction for camera model-specific GPhoto2 behavior."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from openscan_firmware.config.camera import CameraSettings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CameraIdentity:
    model: str | None
    port: str | None


class GPhoto2Profile:
    """Base profile for model-specific GPhoto2 tuning."""

    profile_id = "generic"

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
