"""Unified directory resolution helpers for OpenScan paths."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class PathProfile:
    env_var: str
    system_path: Path
    fallback_path: Path


PATH_PROFILES: dict[str, PathProfile] = {
    "settings": PathProfile(
        env_var="OPENSCAN_SETTINGS_DIR",
        system_path=Path("/etc/openscan3"),
        fallback_path=Path("./settings"),
    ),
    "logs": PathProfile(
        env_var="OPENSCAN_LOG_DIR",
        system_path=Path("/var/log/openscan3"),
        fallback_path=Path("./logs"),
    ),
    "projects": PathProfile(
        env_var="OPENSCAN_PROJECT_DIR",
        system_path=Path("/var/openscan3/projects"),
        fallback_path=Path("./projects"),
    ),
    "tasks": PathProfile(
        env_var="OPENSCAN_TASK_DIR",
        system_path=Path("/var/openscan3/tasks"),
        fallback_path=Path("./tasks/community"),
    ),
}


def _resolve_base_dir(profile_name: str) -> Path:
    """Resolve the base directory for a given profile based on env/system/project fallback."""
    profile = PATH_PROFILES[profile_name]

    env_dir = os.getenv(profile.env_var)
    if env_dir and env_dir.strip():
        return Path(env_dir.strip()).expanduser()

    if profile.system_path.exists():
        return profile.system_path

    return profile.fallback_path


def _resolve_with_optional_subdir(profile_name: str, subdirectory: str | None = None) -> Path:
    base = _resolve_base_dir(profile_name)
    if not subdirectory:
        return base

    candidate = base / subdirectory
    if candidate.exists():
        return candidate

    if base.exists():
        return base

    return candidate


def resolve_settings_dir(subdirectory: str | None = None) -> Path:
    """Resolve the settings directory with optional subdirectory support."""
    return _resolve_with_optional_subdir("settings", subdirectory)


def resolve_settings_file(subdirectory: str, filename: str) -> Path:
    """Build a settings file path within the resolved settings directory."""
    return resolve_settings_dir(subdirectory) / filename


def load_settings_json(filename: str, subdirectory: str | None = None) -> dict[str, Any] | None:
    """Load a JSON settings file from the resolved settings directory."""
    settings_dir = resolve_settings_dir(subdirectory)
    candidate = settings_dir / filename
    if not candidate.exists():
        return None

    try:
        return json.loads(candidate.read_text())
    except Exception:
        LOGGER.exception("Failed to read settings JSON from %s", candidate)
        return None


def resolve_logs_dir() -> Path:
    """Resolve the logs directory respecting OPENSCAN_LOG_DIR overrides."""
    return _resolve_base_dir("logs")


def resolve_projects_dir(subdirectory: str | None = None) -> Path:
    """Resolve the projects directory or an optional child path."""
    return _resolve_with_optional_subdir("projects", subdirectory)


def resolve_tasks_dir(subdirectory: str | None = None) -> Path:
    """Resolve the tasks directory or an optional child path."""
    return _resolve_with_optional_subdir("tasks", subdirectory)
