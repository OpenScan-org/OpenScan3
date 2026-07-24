"""Safe wrapper around the local OpenScan updater CLI."""

from __future__ import annotations

import asyncio
import json
import subprocess
from collections import deque
from pathlib import Path
from typing import Any, Final

from openscan_firmware import __version__
from openscan_firmware.controllers.services.tasks.task_manager import get_task_manager
from openscan_firmware.models.task import TaskStatus

BACKEND_API_VERSION: Final = "1"
UPDATER_TIMEOUT_SECONDS: Final = {
    "status": 30,
    "update_status": 30,
    "check": 90,
    "check_openscan": 90,
    "check_system": 180,
    "update_openscan": 900,
    "update_system": 1800,
    "repair_openscan3": 1200,
    "healthcheck": 60,
}
MAX_ERROR_TEXT_CHARS: Final = 4096
MAX_LOG_LINES: Final = 200
MAX_LOG_BYTES: Final = 128 * 1024
UPDATER_ENV: Final = {
    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
}

UPDATER_COMMANDS: Final[dict[str, list[str]]] = {
    "status": ["sudo", "/usr/bin/openscan-updater", "status", "--json"],
    "update_status": ["sudo", "/usr/bin/openscan-updater", "system", "status", "--json"],
    "check": ["sudo", "/usr/bin/openscan-updater", "update", "--dry-run", "--json"],
    "check_openscan": ["sudo", "/usr/bin/openscan-updater", "update", "--dry-run", "--json"],
    "check_system": ["sudo", "/usr/bin/openscan-updater", "system", "check", "--json"],
    "update_openscan": ["sudo", "/usr/bin/openscan-updater", "update", "--json"],
    "update_system": ["sudo", "/usr/bin/openscan-updater", "system", "update", "--json"],
    "repair_openscan3": ["sudo", "/usr/bin/openscan-updater", "repair", "--json"],
    "healthcheck": ["sudo", "/usr/bin/openscan-updater", "healthcheck", "--json"],
}

UPDATER_LOG_FILES: Final[tuple[Path, ...]] = (
    Path("/var/log/openscan-updater/updater.log"),
    Path("/var/log/openscan-updater.log"),
)

_update_lock = asyncio.Lock()


class UpdateConflictError(RuntimeError):
    """Raised when an update cannot be started because another operation is active."""

    def __init__(self, error_type: str, message: str) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.message = message


def _base_response(command: str) -> dict[str, Any]:
    return {
        "backend_api_version": BACKEND_API_VERSION,
        "firmware_version": __version__,
        "command": command,
    }


def _error_response(command: str, error_type: str, message: str, **extra: Any) -> dict[str, Any]:
    payload = _base_response(command)
    payload["ok"] = False
    payload["error"] = {"type": error_type, "message": message, **extra}
    return payload


def _result_response(command: str, result: Any, *, ok: bool = True) -> dict[str, Any]:
    payload = _base_response(command)
    payload["ok"] = ok
    payload["result"] = result
    return payload


async def run_updater_command(command: str) -> tuple[int, dict[str, Any]]:
    """Run one fixed updater command and return an HTTP status plus JSON payload."""
    try:
        argv = UPDATER_COMMANDS[command]
    except KeyError:
        return 501, _error_response(
            command,
            "command_not_implemented",
            f"Updater command is not implemented by this backend: {command}",
        )

    timeout = UPDATER_TIMEOUT_SECONDS[command]
    try:
        completed = await asyncio.to_thread(
            subprocess.run,
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            shell=False,
            env=UPDATER_ENV,
        )
    except subprocess.TimeoutExpired:
        return 500, _error_response(
            command,
            "command_timeout",
            f"openscan-updater command timed out after {timeout} seconds",
            timeout_seconds=timeout,
        )
    except OSError as exc:
        return 500, _error_response(
            command,
            "command_execution_failed",
            "openscan-updater could not be executed",
            detail=str(exc),
        )

    stdout = completed.stdout.strip()
    stderr = completed.stderr.strip()

    try:
        result = json.loads(stdout) if stdout else None
    except json.JSONDecodeError:
        return 500, _error_response(
            command,
            "invalid_updater_json",
            "openscan-updater did not return valid JSON",
            returncode=completed.returncode,
            stderr=_truncate_text(stderr),
        )

    if completed.returncode != 0:
        payload = _result_response(command, result, ok=False)
        payload["error"] = {
            "type": "command_failed",
            "message": "openscan-updater exited with a non-zero status",
            "returncode": completed.returncode,
            "stderr": _truncate_text(stderr),
        }
        nonzero_json_results = {
            "check_openscan",
            "check_system",
            "update_openscan",
            "update_system",
            "repair_openscan3",
        }
        if command in nonzero_json_results and result is not None:
            return 200, payload
        return 500, payload

    return 200, _result_response(command, result, ok=True)


async def run_update_check() -> tuple[int, dict[str, Any]]:
    """Return a combined OpenScan and system update plan for the UI."""
    openscan = await _run_update_stage("openscan", "check_openscan")
    system = await _run_update_stage("system", "check_system")
    ok = _stage_ok(openscan) and _stage_ok(system)

    return 200, _result_response(
        "update_check",
        {
            "summary": _combined_summary(openscan, system),
            "stages": {
                "openscan": openscan,
                "system": system,
            },
        },
        ok=ok,
    )


async def read_user_update_status() -> tuple[int, dict[str, Any]]:
    """Return the compact cached update status intended for the web client."""
    status_code, payload = await run_updater_command("update_status")
    if status_code != 200 or not payload.get("ok"):
        return status_code, _public_error("status_unavailable")
    return 200, _public_update_status(payload.get("result"))


async def refresh_user_update_status() -> tuple[int, dict[str, Any]]:
    """Synchronously refresh the cached update status and return its public form."""
    status_code, payload = await run_updater_command("check_system")
    if status_code != 200 or not payload.get("ok"):
        return status_code, _public_error("check_failed")
    return 200, _public_update_status(payload.get("result"))


async def run_update_openscan() -> tuple[int, dict[str, Any]]:
    """Run the OpenScan update command under a process-local lock."""
    if _update_lock.locked():
        return 409, _error_response(
            "update_openscan",
            "update_active",
            "Another update command is already running.",
        )

    if is_scan_active():
        raise UpdateConflictError(
            "scan_active",
            "Updates cannot be started while a scan is running.",
        )

    await _update_lock.acquire()
    try:
        return await run_updater_command("update_openscan")
    finally:
        _update_lock.release()


async def run_update_apply() -> tuple[int, dict[str, Any]]:
    """Run the user-facing update flow: OpenScan first, then system updates."""
    if _update_lock.locked():
        return 409, _error_response(
            "update_apply",
            "update_active",
            "Another update command is already running.",
        )

    if is_scan_active():
        raise UpdateConflictError(
            "scan_active",
            "Updates cannot be started while a scan is running.",
        )

    await _update_lock.acquire()
    try:
        openscan = await _run_update_stage("openscan", "update_openscan")
        stages: dict[str, Any] = {"openscan": openscan}
        if not _stage_ok(openscan):
            return 200, _result_response(
                "update_apply",
                {
                    "summary": "OpenScan update did not complete; system update was not started.",
                    "stages": stages,
                },
                ok=False,
            )

        system_check = await _run_update_stage("system_check", "check_system")
        stages["system_check"] = system_check
        if not _stage_ok(system_check):
            return 200, _result_response(
                "update_apply",
                {
                    "summary": (
                        "System update check did not complete; "
                        "system update was not started."
                    ),
                    "stages": stages,
                },
                ok=False,
            )

        system = await _run_update_stage("system", "update_system")
        stages["system"] = system
        ok = _stage_ok(system)

        return 200, _result_response(
            "update_apply",
            {
                "summary": _apply_summary(ok),
                "stages": stages,
            },
            ok=ok,
        )
    finally:
        _update_lock.release()


async def run_user_update_apply() -> tuple[int, dict[str, Any]]:
    """Apply updates while keeping updater implementation details out of the API."""
    status_code, payload = await run_update_apply()
    if status_code != 200:
        return status_code, _public_error("install_failed")
    if not payload.get("ok"):
        return 200, {"status": "install_failed", "reboot_required": False}

    result = payload.get("result", {})
    stages = result.get("stages", {}) if isinstance(result, dict) else {}
    system = stages.get("system", {}) if isinstance(stages, dict) else {}
    system_payload = system.get("payload", {}) if isinstance(system, dict) else {}
    updater_result = system_payload.get("result", {}) if isinstance(system_payload, dict) else {}
    return 200, {
        "status": "completed",
        "reboot_required": bool(updater_result.get("reboot_required")) if isinstance(updater_result, dict) else False,
    }


async def run_repair_openscan3() -> tuple[int, dict[str, Any]]:
    """Run the OpenScan3 repair command under the shared update/repair lock."""
    if _update_lock.locked():
        return 409, _error_response(
            "repair_openscan3",
            "update_active",
            "Another update or repair command is already running.",
        )

    if is_scan_active():
        raise UpdateConflictError(
            "scan_active",
            "Repair cannot be started while a scan is running.",
        )

    await _update_lock.acquire()
    try:
        return await run_updater_command("repair_openscan3")
    finally:
        _update_lock.release()


def is_scan_active() -> bool:
    """Return true when the firmware task manager reports an active scan task."""
    active_statuses = {TaskStatus.PENDING, TaskStatus.RUNNING, TaskStatus.PAUSED}
    try:
        tasks = get_task_manager().get_all_tasks_info()
    except Exception:
        return False

    return any(task.task_type == "scan_task" and task.status in active_statuses for task in tasks)


async def _run_update_stage(stage: str, command: str) -> dict[str, Any]:
    status_code, payload = await run_updater_command(command)
    result: dict[str, Any] = {
        "stage": stage,
        "command": command,
        "status_code": status_code,
        "ok": _payload_ok(payload),
        "payload": payload,
    }
    if status_code >= 400:
        result["ok"] = False
    return result


def _stage_ok(stage: dict[str, Any]) -> bool:
    return bool(stage.get("ok")) and int(stage.get("status_code", 500)) < 400


def _payload_ok(payload: dict[str, Any]) -> bool:
    if payload.get("ok") is False:
        return False
    result = payload.get("result")
    if isinstance(result, dict) and result.get("ok") is False:
        return False
    return True


def _combined_summary(openscan: dict[str, Any], system: dict[str, Any]) -> str:
    if _stage_ok(openscan) and _stage_ok(system):
        return "OpenScan and system update checks completed."
    if not _stage_ok(openscan) and not _stage_ok(system):
        return "OpenScan and system update checks did not complete."
    if not _stage_ok(openscan):
        return "OpenScan update check did not complete."
    return "System update check did not complete."


def _apply_summary(ok: bool) -> str:
    if ok:
        return "OpenScan update and system update completed."
    return "System update did not complete."


def _public_update_status(payload: Any) -> dict[str, Any]:
    """Project the updater cache into the stable, user-facing API contract."""
    if not isinstance(payload, dict):
        return _public_error("status_unavailable")

    openscan = payload.get("openscan", {})
    system = payload.get("system", {})
    packages = openscan.get("packages", []) if isinstance(openscan, dict) else []
    return {
        "status": _public_status_value(payload.get("status")),
        "checked_at": payload.get("checked_at") if isinstance(payload.get("checked_at"), str) else None,
        "stale": bool(payload.get("stale")),
        "release_channel": _release_channel(payload.get("release_channel")),
        "openscan": {
            "updates_available": bool(openscan.get("updates_available")) if isinstance(openscan, dict) else False,
            "packages": [_public_package(item) for item in packages if isinstance(item, dict)],
        },
        "system": {
            "updates_available": bool(system.get("updates_available")) if isinstance(system, dict) else False,
            "count": _nonnegative_int(system.get("count")) if isinstance(system, dict) else 0,
            "reboot_required_after_install": bool(system.get("reboot_required_after_install")) if isinstance(system, dict) else False,
        },
        "reboot_required": bool(payload.get("reboot_required")),
    }


def _public_error(status: str) -> dict[str, Any]:
    return {
        "status": status,
        "checked_at": None,
        "stale": True,
        "release_channel": "unknown",
        "openscan": {"updates_available": False, "packages": []},
        "system": {"updates_available": False, "count": 0, "reboot_required_after_install": False},
        "reboot_required": False,
    }


def _public_package(package: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(package.get("id", "unknown")),
        "installed_version": package.get("installed_version") if isinstance(package.get("installed_version"), str) else None,
        "available_version": package.get("available_version") if isinstance(package.get("available_version"), str) else None,
        "update_available": bool(package.get("update_available")),
    }


def _public_status_value(value: Any) -> str:
    return value if value in {"unknown", "up_to_date", "updates_available"} else "unknown"


def _release_channel(value: Any) -> str:
    return value if value in {"stable", "nightly", "unknown"} else "unknown"


def _nonnegative_int(value: Any) -> int:
    return value if isinstance(value, int) and value >= 0 else 0


def read_updater_logs(tail: int = MAX_LOG_LINES) -> tuple[int, dict[str, Any]]:
    """Return recent updater logs from fixed known log files only."""
    tail = max(1, min(tail, MAX_LOG_LINES))
    log_path = next((path for path in UPDATER_LOG_FILES if path.is_file()), None)
    if log_path is None:
        return 404, _error_response(
            "logs",
            "log_file_not_found",
            "No updater log file was found.",
        )

    try:
        lines = _tail_text_file(log_path, tail)
    except OSError as exc:
        return 500, _error_response(
            "logs",
            "log_read_failed",
            "Updater log file could not be read.",
            detail=str(exc),
        )

    return 200, _result_response(
        "logs",
        {
            "path": str(log_path),
            "tail": tail,
            "lines": lines,
            "truncated_bytes": MAX_LOG_BYTES,
        },
        ok=True,
    )


def _tail_text_file(path: Path, max_lines: int) -> list[str]:
    with path.open("rb") as handle:
        handle.seek(0, 2)
        size = handle.tell()
        handle.seek(max(0, size - MAX_LOG_BYTES))
        text = handle.read(MAX_LOG_BYTES).decode("utf-8", errors="replace")

    return list(deque(text.splitlines(), maxlen=max_lines))


def _truncate_text(text: str) -> str:
    if len(text) <= MAX_ERROR_TEXT_CHARS:
        return text
    return text[:MAX_ERROR_TEXT_CHARS] + "... [truncated]"
