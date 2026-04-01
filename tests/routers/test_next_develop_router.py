"""Tests for next develop router endpoints."""

from __future__ import annotations

from pathlib import Path
import subprocess

from fastapi import FastAPI
from fastapi.testclient import TestClient

from openscan_firmware.routers.next import develop as develop_router


def _create_app() -> FastAPI:
    app = FastAPI()
    app.include_router(develop_router.router, prefix="/next")
    return app


def test_camera_report_returns_json(monkeypatch, tmp_path: Path):
    script = tmp_path / "camera_report.sh"
    script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    monkeypatch.setattr(develop_router, "CAMERA_REPORT_SCRIPT", script)

    def fake_run(cmd, capture_output, text, timeout, check):  # noqa: ANN001
        assert cmd == ["bash", str(script)]
        assert capture_output is True
        assert text is True
        assert timeout == 180
        assert check is False
        return subprocess.CompletedProcess(cmd, 0, stdout="camera report\n", stderr="")

    monkeypatch.setattr(develop_router.subprocess, "run", fake_run)
    monkeypatch.setattr(
        develop_router,
        "_collect_gphoto2_diagnostics",
        lambda: {"available": True, "error": None, "detected": [], "cameras": []},
    )

    with TestClient(_create_app()) as client:
        response = client.get("/next/develop/camera-report")

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "return_code": 0,
        "script": str(script),
        "report": "camera report",
        "stderr": "",
        "gphoto2": {"available": True, "error": None, "detected": [], "cameras": []},
    }


def test_camera_report_missing_script_returns_404(monkeypatch, tmp_path: Path):
    missing_script = tmp_path / "missing_camera_report.sh"
    monkeypatch.setattr(develop_router, "CAMERA_REPORT_SCRIPT", missing_script)

    with TestClient(_create_app()) as client:
        response = client.get("/next/develop/camera-report")

    assert response.status_code == 404
    assert "Camera report script not found" in response.json()["detail"]


def test_camera_report_text_includes_gphoto2_section(monkeypatch, tmp_path: Path):
    script = tmp_path / "camera_report.sh"
    script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    monkeypatch.setattr(develop_router, "CAMERA_REPORT_SCRIPT", script)
    monkeypatch.setattr(
        develop_router.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, stdout="report body\n", stderr=""),
    )
    monkeypatch.setattr(
        develop_router,
        "_collect_gphoto2_diagnostics",
        lambda: {"available": False, "error": "missing", "detected": [], "cameras": []},
    )

    with TestClient(_create_app()) as client:
        response = client.get("/next/develop/camera-report?format=text")

    assert response.status_code == 200
    assert "report body" in response.text
    assert "===== GPhoto2 python diagnostics =====" in response.text
    assert "\"available\": false" in response.text
