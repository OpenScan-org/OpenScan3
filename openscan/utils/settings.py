"""Utility helpers for resolving OpenScan settings directories and files."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Iterable, List


LOGGER = logging.getLogger(__name__)


def resolve_settings_dir(subdirectory: str | None = None) -> Path:
    """Return the preferred settings directory, optionally within a subdirectory.

    Precedence order:
    1. OPENSCAN_SETTINGS_DIR environment variable (always preferred if present)
    2. /etc/openscan3
    3. ./settings

    Args:
        subdirectory: Optional subdirectory name to append to the resolved base path.

    Returns:
        Path: Resolved directory path (the directory may not exist yet).
    """
    env_dir = os.getenv("OPENSCAN_SETTINGS_DIR")
    if env_dir:
        paths = [p for p in (part.strip() for part in env_dir.split(":")) if p]
        if paths:
            base = Path(paths[0])
            return base / subdirectory if subdirectory else base

    etc_base = Path("/etc/openscan3")
    etc_candidate = etc_base / subdirectory if subdirectory else etc_base
    if etc_candidate.exists():
        return etc_candidate

    project_base = Path("./settings")
    return project_base / subdirectory if subdirectory else project_base


def resolve_settings_file(subdirectory: str, filename: str) -> Path:
    """Build a settings file path within the resolved settings directory.

    Args:
        subdirectory: Subdirectory that should contain the file (e.g., "device").
        filename: File name to append inside the subdirectory.

    Returns:
        Path: Full path pointing to the requested settings file.
    """
    return resolve_settings_dir(subdirectory) / filename


def load_settings_json(filename: str, subdirectory: str | None = None) -> dict[str, Any] | None:
    """Load a JSON settings file from the resolved settings directory.

    Args:
        filename: Name of the JSON file to read.
        subdirectory: Optional subdirectory to search within.

    Returns:
        Parsed JSON dictionary if the file exists and can be read, otherwise None.
    """

    settings_dir = resolve_settings_dir(subdirectory)
    candidate = settings_dir / filename
    if not candidate.exists():
        return None

    try:
        return json.loads(candidate.read_text())
    except Exception:
        LOGGER.exception("Failed to read settings JSON from %s", candidate)
        return None


def iter_settings_dirs(subdirectory: str | None = None, *, existing_only: bool = True) -> list[Path]:
    """Return settings directories in precedence order.

    Args:
        subdirectory: Optional subdirectory name to append.
        existing_only: If True, skip directories that do not exist.

    Returns:
        List of Path objects ordered by precedence.
    """

    resolved: list[Path] = []
    seen: set[Path] = set()

    def _add_candidate(base_path: Path) -> None:
        candidate = base_path / subdirectory if subdirectory else base_path
        if existing_only and not candidate.exists():
            return
        if candidate not in seen:
            resolved.append(candidate)
            seen.add(candidate)

    env_dir = os.getenv("OPENSCAN_SETTINGS_DIR")
    if env_dir:
        for raw in env_dir.split(":"):
            raw = raw.strip()
            if not raw:
                continue
            _add_candidate(Path(raw))

    for base in (Path("/etc/openscan3"), Path("./settings")):
        _add_candidate(base)

    return resolved
