"""Tests for the develop router endpoints."""

from __future__ import annotations

import importlib
from itertools import count

from fastapi.testclient import TestClient

from openscan.cli import _cmd_serve
import openscan.main as main_module
from openscan.routers import develop


def test_restart_endpoint_updates_reload_trigger(monkeypatch, tmp_path):
    """The restart endpoint should create and update the reload sentinel file."""
    sentinel_file = tmp_path / "reload.trigger"
    monkeypatch.setattr(develop, "RELOAD_TRIGGER_FILE", sentinel_file)

    time_values = count(start=1)
    monkeypatch.setattr(develop.time, "time", lambda: float(next(time_values)))

    with TestClient(main_module.app) as client:
        first_response = client.post("/latest/develop/restart")
        assert first_response.status_code == 202
        assert first_response.json() == {"detail": "Reload triggered"}
        assert sentinel_file.exists()

        first_contents = sentinel_file.read_text(encoding="utf-8")
        first_mtime = sentinel_file.stat().st_mtime

        second_response = client.post("/latest/develop/restart")
        assert second_response.status_code == 202
        assert second_response.json() == {"detail": "Reload triggered"}
        assert sentinel_file.exists()

        second_contents = sentinel_file.read_text(encoding="utf-8")
        second_mtime = sentinel_file.stat().st_mtime

    assert second_contents != first_contents
    assert second_mtime >= first_mtime


def test_cli_reload_trigger_configures_uvicorn(monkeypatch, tmp_path):
    """A reload trigger should force uvicorn to watch only the sentinel file."""

    captured = {}

    def fake_run(app, **params):  # noqa: ANN001
        captured["app"] = app
        captured["params"] = params
        return 0

    monkeypatch.setattr("openscan.cli.uvicorn.run", fake_run)

    trigger_path = tmp_path / "reload.trigger"
    monkeypatch.setattr("openscan.cli.DEFAULT_RELOAD_TRIGGER", trigger_path, raising=False)

    exit_code = _cmd_serve(
        host="127.0.0.1",
        port=1234,
        reload_trigger=True,
    )

    assert exit_code == 0
    assert captured["app"] == "openscan.main:app"

    params = captured["params"]
    assert params["host"] == "127.0.0.1"
    assert params["port"] == 1234
    assert params["reload"] is True
    assert params["reload_dirs"] == [str(trigger_path.parent)]
    assert params["reload_includes"] == [trigger_path.name]
    assert params["reload_excludes"] == ["*.py", "*.pyc", "*.pyi", "*.pyd", "*.pyo"]


def test_restart_triggers_device_initialize_on_reload(monkeypatch, tmp_path):
    """Touching the reload endpoint should trigger re-initialization after reload."""

    sentinel_file = tmp_path / "reload.trigger"
    monkeypatch.setattr(develop, "RELOAD_TRIGGER_FILE", sentinel_file)

    init_calls: list[object] = []

    def fake_load_config():  # noqa: ANN202
        return {"dummy": True}

    def fake_initialize(config):  # noqa: ANN201
        init_calls.append(config)

    monkeypatch.setattr("openscan.controllers.device.load_device_config", fake_load_config)
    monkeypatch.setattr("openscan.controllers.device.initialize", fake_initialize)

    # First app lifecycle run (baseline)
    with TestClient(main_module.app) as client:
        assert len(init_calls) == 1
        response = client.post("/latest/develop/restart")
        assert response.status_code == 202

    # Reload the main module to simulate uvicorn reload picking up the sentinel change
    reloaded_main = importlib.reload(main_module)

    with TestClient(reloaded_main.app):
        pass

    assert len(init_calls) == 2
