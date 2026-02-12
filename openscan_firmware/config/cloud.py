"""Helpers and model for managing cloud service configuration."""

from __future__ import annotations

import os
from typing import Mapping

from pydantic import BaseModel, Field, HttpUrl


DEFAULT_CLOUD_USER = "openscan"
DEFAULT_CLOUD_PASSWORD = "free"
DEFAULT_CLOUD_HOST = "http://openscanfeedback.dnsuser.de:1334"
DEFAULT_SPLIT_SIZE = 200_000_000


class CloudSettings(BaseModel):
    """Settings that describe how to talk to the OpenScan cloud backend."""

    user: str = Field(
        DEFAULT_CLOUD_USER,
        description="HTTP basic auth username for the cloud API.",
    )
    password: str = Field(
        DEFAULT_CLOUD_PASSWORD,
        description="HTTP basic auth password for the cloud API.",
    )
    token: str = Field(..., description="API token identifying the device or user.")
    host: HttpUrl = Field(
        DEFAULT_CLOUD_HOST,
        description="Base URL of the cloud service.",
    )
    split_size: int = Field(
        DEFAULT_SPLIT_SIZE,
        ge=1,
        description=(
            "Maximum upload part size in bytes. The cloud currently accepts up to 200 MB per chunk."
        ),
    )


class CloudConfigurationError(RuntimeError):
    """Raised when cloud settings are not configured but required."""


_active_cloud_settings: CloudSettings | None = None


def set_cloud_settings(settings: CloudSettings | None) -> None:
    """Register the active cloud settings for the running application."""

    global _active_cloud_settings
    _active_cloud_settings = settings


def get_cloud_settings() -> CloudSettings:
    """Return the active cloud settings or raise if they are missing."""

    if _active_cloud_settings is None:
        raise CloudConfigurationError(
            "Cloud settings have not been initialized. Call set_cloud_settings() during startup."
        )
    return _active_cloud_settings


def load_cloud_settings_from_env(env: Mapping[str, str] | None = None) -> CloudSettings | None:
    """Create cloud settings from environment variables.

    Only the API token may be provided via the environment. All other values
    fall back to their project defaults.

    Args:
        env: Optional mapping to read values from, defaults to ``os.environ``.

    Returns:
        CloudSettings instance when the token variable is present, otherwise ``None``.
    """

    source = env or os.environ

    token = source.get("OPENSCANCLOUD_TOKEN", "").strip()
    if not token:
        return None

    return CloudSettings(
        user=DEFAULT_CLOUD_USER,
        password=DEFAULT_CLOUD_PASSWORD,
        token=token,
        host=DEFAULT_CLOUD_HOST,
        split_size=DEFAULT_SPLIT_SIZE,
    )


def mask_secret(value: str | None) -> str:
    """Return a masked representation for secrets in logs."""

    if not value:
        return "(empty)"
    if len(value) <= 4:
        return "***"
    return f"***{value[-4:]}"
