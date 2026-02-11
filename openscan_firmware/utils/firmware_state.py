"""Firmware state lockfile helpers.

This module persists minimal runtime health information (unclean shutdowns,
last firmware version, schema revision) inside a lockfile under the runtime
directory. The helpers provide atomic read/write operations, state caching and
startup/shutdown utilities so higher layers can reliably detect crashes and
flag canary conditions during firmware updates.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from openscan_firmware import __version__
from openscan_firmware.utils.dir_paths import resolve_runtime_dir

LOGGER = logging.getLogger(__name__)
STATE_SCHEMA_VERSION = 1
STATE_PATH = resolve_runtime_dir() / "firmware_state.lock"
_STATE_CACHE: Dict[str, Any] | None = None


def _default_state() -> Dict[str, Any]:
    return {
        "version": STATE_SCHEMA_VERSION,
        "unclean_shutdown": False,
        "updated_at": None,
        "last_shutdown_was_unclean": False,
        "last_seen_firmware_version": __version__,
    }


def _now_iso() -> str:
    return (
        datetime.now(tz=timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _ensure_parent() -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)


def _write_state(state: Dict[str, Any]) -> Dict[str, Any]:
    global _STATE_CACHE

    _ensure_parent()
    state["version"] = STATE_SCHEMA_VERSION
    state["last_seen_firmware_version"] = __version__
    state.setdefault("updated_at", _now_iso())
    tmp_path = STATE_PATH.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp_path.replace(STATE_PATH)
    _STATE_CACHE = dict(state)
    return dict(state)


def _load_state() -> Dict[str, Any]:
    global _STATE_CACHE

    if _STATE_CACHE is not None:
        return dict(_STATE_CACHE)

    if not STATE_PATH.exists():
        state = _default_state()
        _STATE_CACHE = dict(state)
        return state

    try:
        loaded = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        merged = {**_default_state(), **loaded}
        _STATE_CACHE = dict(merged)
        return dict(merged)
    except (json.JSONDecodeError, OSError) as exc:
        LOGGER.warning("Failed to read firmware state lock %s: %s", STATE_PATH, exc)
        state = _default_state()
        _STATE_CACHE = dict(state)
        return state


def get_firmware_state() -> Dict[str, Any]:
    return _load_state()


def mark_unclean_shutdown() -> Dict[str, Any]:
    state = _load_state()
    state["last_shutdown_was_unclean"] = state.get("unclean_shutdown", False)
    state["unclean_shutdown"] = True
    state["updated_at"] = _now_iso()
    return _write_state(state)


def mark_clean_shutdown() -> Dict[str, Any]:
    state = _load_state()
    state["unclean_shutdown"] = False
    state["last_shutdown_was_unclean"] = False
    state["updated_at"] = _now_iso()
    return _write_state(state)


def handle_startup(logger: logging.Logger | None = None) -> Dict[str, Any]:
    state = _load_state()
    log = logger or LOGGER
    if state.get("unclean_shutdown"):
        log.warning(
            "Previous shutdown was unclean (last update: %s). Manual inspection of motor position recommended.",
            state.get("updated_at") or "unknown",
        )
    return mark_unclean_shutdown()


def override_state_path(path: Path) -> None:
    global STATE_PATH, _STATE_CACHE
    STATE_PATH = path
    _STATE_CACHE = None
