"""Persistence helpers for OpenScan cloud configuration."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from openscan_firmware.config.cloud import CloudConfigurationError, CloudSettings, get_cloud_settings, mask_secret
from openscan_firmware.controllers.services.cloud import _require_cloud_settings
from openscan_firmware.utils.settings import resolve_settings_file

logger = logging.getLogger(__name__)

_CLOUD_SETTINGS_SUBDIR = "openscan"
_CLOUD_SETTINGS_FILENAME = "cloud.json"
_ACTIVE_SOURCE: str | None = None


def get_settings_path() -> Path:
    """Return the path where persistent cloud settings are stored."""

    return resolve_settings_file(_CLOUD_SETTINGS_SUBDIR, _CLOUD_SETTINGS_FILENAME)


def save_persistent_cloud_settings(settings: CloudSettings) -> Path:
    """Persist cloud settings to disk atomically.

    Args:
        settings: Cloud settings to serialise.

    Returns:
        Path to the persisted settings file.
    """

    target = get_settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)

    payload = settings.model_dump()
    temp_path = target.with_suffix(".tmp")

    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.flush()
        os.fsync(handle.fileno())

    temp_path.replace(target)
    logger.debug("Persisted cloud settings to %s", target)
    return target


def load_persistent_cloud_settings() -> CloudSettings | None:
    """Load cloud settings from disk if available."""

    path = get_settings_path()
    if not path.exists():
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return CloudSettings.model_validate(data)
    except Exception:  # pragma: no cover - logged for observability
        logger.exception("Failed to read cloud settings from %s", path)
        return None


def settings_file_exists() -> bool:
    """Return whether persistent cloud settings are available."""

    return get_settings_path().exists()


def mask_cloud_settings(settings: CloudSettings) -> dict[str, Any]:
    """Return a masked representation of the provided cloud settings."""

    return {
        "host": str(settings.host),
        "split_size": settings.split_size,
        "user": mask_secret(settings.user),
        "password": mask_secret(settings.password),
        "token": mask_secret(settings.token),
    }


def set_active_source(source: str | None) -> None:
    """Remember where the active cloud settings originated from."""

    global _ACTIVE_SOURCE
    _ACTIVE_SOURCE = source


def get_active_source() -> str | None:
    """Return the origin of the active cloud settings."""

    return _ACTIVE_SOURCE


def get_active_cloud_settings() -> CloudSettings | None:
    """Return currently active cloud settings if configured."""

    try:
        return get_cloud_settings()
    except CloudConfigurationError:
        return None


def get_masked_active_settings() -> dict[str, Any] | None:
    """Return masked active settings if available."""

    settings = get_active_cloud_settings()
    if settings is None:
        return None
    return mask_cloud_settings(settings)
