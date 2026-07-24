from __future__ import annotations

import asyncio
import subprocess

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openscan_firmware.models.task import Task, TaskStatus
from openscan_firmware.routers.system_update import router
from openscan_firmware.routers.system_repair import router as repair_router
from openscan_firmware import system_update


@pytest.fixture
def update_client() -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/latest")
    app.include_router(repair_router, prefix="/latest")
    with TestClient(app) as client:
        yield client


def _completed(argv: list[str], stdout: str, stderr: str = "", returncode: int = 0):
    return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr=stderr)


def test_fixed_command_map_has_no_request_controlled_package_names():
    assert system_update.UPDATER_COMMANDS == {
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
    assert all("apt" not in argv for command in system_update.UPDATER_COMMANDS.values() for argv in command)


def test_status_endpoint_returns_compact_cached_update_status(monkeypatch, update_client):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return _completed(
            argv,
            '{"status":"updates_available","checked_at":"2026-07-24T09:15:00Z",'
            '"stale":false,"release_channel":"nightly",'
            '"openscan":{"updates_available":true,"packages":[{"id":"updater",'
            '"installed_version":"0.1.8","available_version":"0.1.9","update_available":true}]},'
            '"system":{"updates_available":true,"count":2,"reboot_required_after_install":false},'
            '"reboot_required":false}',
        )

    monkeypatch.setattr(system_update.subprocess, "run", fake_run)

    response = update_client.get("/latest/system/update/status")

    assert response.status_code == 200
    assert response.json() == {
        "status": "updates_available",
        "checked_at": "2026-07-24T09:15:00Z",
        "stale": False,
        "release_channel": "nightly",
        "openscan": {
            "updates_available": True,
            "packages": [{"id": "updater", "installed_version": "0.1.8", "available_version": "0.1.9", "update_available": True}],
        },
        "system": {"updates_available": True, "count": 2, "reboot_required_after_install": False},
        "reboot_required": False,
    }
    assert calls[0][0] == system_update.UPDATER_COMMANDS["update_status"]
    assert calls[0][1]["shell"] is False
    assert calls[0][1]["env"] == system_update.UPDATER_ENV


def test_check_endpoint_returns_compact_refreshed_status(monkeypatch, update_client):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return _completed(argv, '{"status":"up_to_date","checked_at":"2026-07-24T09:15:00Z","stale":false,"release_channel":"stable","openscan":{"updates_available":false,"packages":[]},"system":{"updates_available":false,"count":0,"reboot_required_after_install":false},"reboot_required":false}')

    monkeypatch.setattr(system_update.subprocess, "run", fake_run)

    response = update_client.post("/latest/system/update/check", json={"ignored": "input"})

    assert response.status_code == 200
    assert response.json()["status"] == "up_to_date"
    assert response.json()["release_channel"] == "stable"
    assert calls == [system_update.UPDATER_COMMANDS["check_system"]]


def test_router_exposes_only_compact_update_actions(update_client):
    assert update_client.post("/latest/system/update/openscan").status_code == 404
    assert update_client.post("/latest/system/update/healthcheck").status_code == 404
    assert update_client.get("/latest/system/update/logs").status_code == 404


def test_update_openapi_exposes_response_schemas(update_client):
    schema = update_client.get("/openapi.json").json()
    paths = schema["paths"]

    assert paths["/latest/system/update/status"]["get"]["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/UpdateStatusResponse"
    }
    assert paths["/latest/system/update/check"]["post"]["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/UpdateStatusResponse"
    }
    assert paths["/latest/system/update/apply"]["post"]["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/UpdateInstallResponse"
    }


def test_updater_command_timeout_returns_structured_error(monkeypatch, update_client):
    def fake_run(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, timeout=kwargs["timeout"])

    monkeypatch.setattr(system_update.subprocess, "run", fake_run)

    response = update_client.get("/latest/system/update/status")

    assert response.status_code == 500
    payload = response.json()
    assert payload["status"] == "status_unavailable"
    assert payload["stale"] is True


def test_invalid_json_returns_structured_error(monkeypatch, update_client):
    def fake_run(argv, **kwargs):
        return _completed(argv, "not json")

    monkeypatch.setattr(system_update.subprocess, "run", fake_run)

    response = update_client.get("/latest/system/update/status")

    assert response.status_code == 500
    assert response.json()["status"] == "status_unavailable"


def test_nonzero_exit_returns_structured_error(monkeypatch, update_client):
    def fake_run(argv, **kwargs):
        return _completed(argv, '{"ok": false}', "apt failed", returncode=1)

    monkeypatch.setattr(system_update.subprocess, "run", fake_run)

    response = update_client.post("/latest/system/update/check")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "check_failed"
    assert payload["stale"] is True


@pytest.mark.asyncio
async def test_concurrent_update_attempts_are_rejected(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()

    async def fake_run(command: str):
        started.set()
        await release.wait()
        return 200, {"ok": True, "command": command, "result": {}}

    monkeypatch.setattr(system_update, "is_scan_active", lambda: False)
    monkeypatch.setattr(system_update, "run_updater_command", fake_run)

    first = asyncio.create_task(system_update.run_update_openscan())
    await started.wait()
    second_status, second_payload = await system_update.run_update_openscan()
    release.set()
    first_status, _ = await first

    assert first_status == 200
    assert second_status == 409
    assert second_payload["error"]["type"] == "update_active"


def test_apply_endpoint_runs_openscan_then_system_check_then_system_update(
    monkeypatch,
    update_client,
):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        if argv == system_update.UPDATER_COMMANDS["update_openscan"]:
            return _completed(argv, '{"ok": true, "updated": ["openscan3-updater"]}')
        if argv == system_update.UPDATER_COMMANDS["check_system"]:
            return _completed(argv, '{"ok": true, "classification": "allowed_userland"}')
        return _completed(argv, '{"ok": true, "updated": ["rpi-swap"]}')

    monkeypatch.setattr(system_update, "is_scan_active", lambda: False)
    monkeypatch.setattr(system_update.subprocess, "run", fake_run)

    response = update_client.post("/latest/system/update/apply")

    assert response.status_code == 200
    payload = response.json()
    assert payload == {"status": "completed", "reboot_required": False}
    assert calls == [
        system_update.UPDATER_COMMANDS["update_openscan"],
        system_update.UPDATER_COMMANDS["check_system"],
        system_update.UPDATER_COMMANDS["update_system"],
    ]


def test_apply_endpoint_stops_before_system_when_openscan_fails(monkeypatch, update_client):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return _completed(argv, '{"ok": false, "classification": "blocked"}', returncode=2)

    monkeypatch.setattr(system_update, "is_scan_active", lambda: False)
    monkeypatch.setattr(system_update.subprocess, "run", fake_run)

    response = update_client.post("/latest/system/update/apply")

    assert response.status_code == 200
    payload = response.json()
    assert payload == {"status": "install_failed", "reboot_required": False}
    assert calls == [system_update.UPDATER_COMMANDS["update_openscan"]]


def test_apply_endpoint_blocks_when_scan_active(monkeypatch, update_client):
    monkeypatch.setattr(system_update, "is_scan_active", lambda: True)

    response = update_client.post("/latest/system/update/apply")

    assert response.status_code == 409
    assert response.json()["status"] == "install_blocked"


def test_is_scan_active_uses_task_manager(monkeypatch):
    manager = type(
        "Manager",
        (),
        {
            "get_all_tasks_info": lambda self: [
                Task(name="scan", task_type="scan_task", status=TaskStatus.RUNNING)
            ]
        },
    )()
    monkeypatch.setattr(system_update, "get_task_manager", lambda: manager)

    assert system_update.is_scan_active() is True


def test_repair_endpoint_calls_fixed_command(monkeypatch, update_client):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return _completed(argv, '{"ok": true, "command": "repair", "steps": []}')

    monkeypatch.setattr(system_update, "is_scan_active", lambda: False)
    monkeypatch.setattr(system_update.subprocess, "run", fake_run)

    response = update_client.post("/latest/system/repair/openscan3")

    assert response.status_code == 200
    assert response.json()["result"]["command"] == "repair"
    assert calls[0][0] == system_update.UPDATER_COMMANDS["repair_openscan3"]
    assert calls[0][1]["shell"] is False


def test_repair_command_failure_returns_parsed_json(monkeypatch, update_client):
    def fake_run(argv, **kwargs):
        return _completed(
            argv,
            '{"ok": false, "error": {"type": "camera_manifest_missing"}}',
            returncode=1,
        )

    monkeypatch.setattr(system_update, "is_scan_active", lambda: False)
    monkeypatch.setattr(system_update.subprocess, "run", fake_run)

    response = update_client.post("/latest/system/repair/openscan3")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert payload["result"]["error"]["type"] == "camera_manifest_missing"


@pytest.mark.asyncio
async def test_concurrent_repair_attempts_are_rejected(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()

    async def fake_run(command: str):
        started.set()
        await release.wait()
        return 200, {"ok": True, "command": command, "result": {}}

    monkeypatch.setattr(system_update, "is_scan_active", lambda: False)
    monkeypatch.setattr(system_update, "run_updater_command", fake_run)

    first = asyncio.create_task(system_update.run_repair_openscan3())
    await started.wait()
    second_status, second_payload = await system_update.run_repair_openscan3()
    release.set()
    first_status, _ = await first

    assert first_status == 200
    assert second_status == 409
    assert second_payload["error"]["type"] == "update_active"


def test_active_scan_guard_blocks_repair(monkeypatch, update_client):
    monkeypatch.setattr(system_update, "is_scan_active", lambda: True)

    response = update_client.post("/latest/system/repair/openscan3")

    assert response.status_code == 409
    assert response.json()["error"]["type"] == "scan_active"


def test_repair_endpoint_not_exposed_by_update_router_alone():
    app = FastAPI()
    app.include_router(router, prefix="/latest")

    with TestClient(app) as client:
        response = client.post("/latest/system/repair/openscan3")

    assert response.status_code == 404
