"""Firmware-level settings that are independent of the hardware configuration.

These settings control firmware behavior such as automatic background tasks,
update preferences, and other global toggles.  They are persisted in
``settings/firmware/firmware_settings.json`` and loaded once at startup.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import BaseModel, Field

from openscan_firmware.utils.dir_paths import resolve_settings_file, resolve_settings_dir

logger = logging.getLogger(__name__)

_SETTINGS_SUBDIR = "firmware"
_SETTINGS_FILENAME = "firmware_settings.json"


class FirmwareSettings(BaseModel):
    """Global firmware behaviour toggles.

    Attributes:
        qr_wifi_scan_enabled: When True the firmware automatically starts the
            QR WiFi scan task on startup if no usable network connection is
            detected.
    """

    qr_wifi_scan_enabled: bool = Field(
        default=True,
        description="Automatically scan for WiFi QR codes on startup when no WiFi or Ethernet connection is active.",
    )


# Module-level singleton – loaded once, then reused.
_firmware_settings: FirmwareSettings | None = None


def get_firmware_settings() -> FirmwareSettings:
    """Return the current firmware settings (loads from disk on first call)."""
    global _firmware_settings
    if _firmware_settings is None:
        _firmware_settings = load_firmware_settings()
    return _firmware_settings


def load_firmware_settings() -> FirmwareSettings:
    """Load firmware settings from disk, falling back to defaults.

    If the settings file does not exist yet it is created with default values
    so the user has a file to edit.

    Returns:
        FirmwareSettings populated from the JSON file or defaults.
    """
    settings_file = resolve_settings_file(_SETTINGS_SUBDIR, _SETTINGS_FILENAME)

    if settings_file.exists():
        try:
            raw = json.loads(settings_file.read_text(encoding="utf-8"))
            settings = FirmwareSettings.model_validate(raw)
            logger.info("Loaded firmware settings from %s", settings_file)
            return settings
        except Exception:
            logger.exception("Failed to parse firmware settings from %s – using defaults", settings_file)

    # File missing or broken → create with defaults
    settings = FirmwareSettings()
    save_firmware_settings(settings)
    return settings


def save_firmware_settings(settings: FirmwareSettings) -> None:
    """Persist firmware settings to disk.

    Args:
        settings: The settings model to write.
    """
    global _firmware_settings
    settings_file = resolve_settings_file(_SETTINGS_SUBDIR, _SETTINGS_FILENAME)
    settings_file.parent.mkdir(parents=True, exist_ok=True)

    settings_file.write_text(
        settings.model_dump_json(indent=4) + "\n",
        encoding="utf-8",
    )
    _firmware_settings = settings
    logger.info("Saved firmware settings to %s", settings_file)
