from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import numpy as np
import pytest
import pytest_asyncio

from openscan_firmware.controllers.services.tasks.task_manager import TaskManager
from openscan_firmware.controllers.services.tasks.core import qr_scan_task as qr_module
from openscan_firmware.models.task import Task, TaskStatus


@pytest_asyncio.fixture
async def qr_task_manager():
    """Provide a clean TaskManager instance with autodiscovered tasks."""

    TaskManager._instance = None
    task_manager = TaskManager()
    task_manager.autodiscover_tasks(
        namespaces=["openscan_firmware.controllers.services.tasks"],
        extra_ignore_modules={"base_task", "task_manager", "example_tasks"},
        override_on_conflict=False,
    )

    yield task_manager

    active_tasks = task_manager.get_all_tasks_info()
    if active_tasks:
        cancellations = [
            task_manager.cancel_task(task.id)
            for task in active_tasks
            if task.status in {TaskStatus.RUNNING, TaskStatus.PENDING, TaskStatus.PAUSED}
        ]
        if cancellations:
            await asyncio.gather(*cancellations, return_exceptions=True)

    TaskManager._instance = None


@pytest.mark.asyncio
async def test_qr_scan_task_connects_wifi_success(monkeypatch, qr_task_manager):
    """Ensure the task detects WiFi QR codes and applies credentials successfully."""

    fake_frame = np.zeros((10, 10, 3), dtype=np.uint8)
    monkeypatch.setattr(qr_module, "_STARTUP_DELAY", 0)
    monkeypatch.setattr(qr_module, "_SCAN_INTERVAL", 0)
    monkeypatch.setattr(qr_module, "_cleanup_stale_qr_tasks", AsyncMock())
    monkeypatch.setattr(qr_module, "_capture_preview_array", AsyncMock(return_value=fake_frame))

    class DummyController:
        async def preview_async(self):  # pragma: no cover
            return b"ignored"

    monkeypatch.setattr(
        "openscan_firmware.controllers.hardware.cameras.camera.get_camera_controller",
        lambda name: DummyController(),
    )

    class DummyConsensus:
        def __init__(self, _reader, required_hits, window):
            self.calls = 0

        def feed(self, _frame):
            self.calls += 1
            if self.calls >= 2:
                return "WIFI:S:TestNet;T:WPA;P:secret;H:false;;"
            return None

    monkeypatch.setattr("openscan_firmware.utils.qr_reader.ZxingQRReader", lambda: object())
    monkeypatch.setattr("openscan_firmware.utils.qr_reader.StableQRConsensus", DummyConsensus)

    def fake_parse_wifi_qr(_text: str) -> SimpleNamespace:
        return SimpleNamespace(ssid="TestNet", security="WPA2", hidden=False)

    def fake_connect_wifi(_credentials):
        return "nmcli success"

    monkeypatch.setattr("openscan_firmware.utils.wifi.parse_wifi_qr", fake_parse_wifi_qr)
    monkeypatch.setattr("openscan_firmware.utils.wifi.connect_wifi", fake_connect_wifi)

    monkeypatch.setattr(qr_module, "get_task_manager", lambda: qr_task_manager)

    task = await qr_task_manager.create_and_run_task("qr_scan_task", camera_name="mock_cam")
    final = await qr_task_manager.wait_for_task(task.id)

    assert final.status == TaskStatus.COMPLETED
    assert final.result == {
        "ssid": "TestNet",
        "security": "WPA2",
        "hidden": False,
        "nmcli_output": "nmcli success",
    }


@pytest.mark.asyncio
async def test_qr_scan_task_wifi_connect_failure_marks_error(monkeypatch, qr_task_manager):
    """Ensure connection errors bubble up and mark the task as ERROR."""

    fake_frame = np.zeros((10, 10, 3), dtype=np.uint8)
    monkeypatch.setattr(qr_module, "_STARTUP_DELAY", 0)
    monkeypatch.setattr(qr_module, "_SCAN_INTERVAL", 0)
    monkeypatch.setattr(qr_module, "_cleanup_stale_qr_tasks", AsyncMock())
    monkeypatch.setattr(qr_module, "_capture_preview_array", AsyncMock(return_value=fake_frame))

    controller = type("DummyController", (), {"preview_async": AsyncMock(return_value=b"bytes")})()
    monkeypatch.setattr(
        "openscan_firmware.controllers.hardware.cameras.camera.get_camera_controller",
        lambda name: controller,
    )

    class AlwaysFoundConsensus:
        def __init__(self, _reader, required_hits, window):
            pass

        def feed(self, _frame):
            return "WIFI:S:BrokenNet;T:WPA;P:secret;H:false;;"

    monkeypatch.setattr("openscan_firmware.utils.qr_reader.ZxingQRReader", lambda: object())
    monkeypatch.setattr("openscan_firmware.utils.qr_reader.StableQRConsensus", AlwaysFoundConsensus)

    def fake_parse_wifi_qr(_text: str) -> SimpleNamespace:
        return SimpleNamespace(ssid="BrokenNet", security="WPA2", hidden=False)

    def failing_connect_wifi(_credentials):
        raise RuntimeError("nmcli failure")

    monkeypatch.setattr("openscan_firmware.utils.wifi.parse_wifi_qr", fake_parse_wifi_qr)
    monkeypatch.setattr("openscan_firmware.utils.wifi.connect_wifi", failing_connect_wifi)

    monkeypatch.setattr(qr_module, "get_task_manager", lambda: qr_task_manager)

    task = await qr_task_manager.create_and_run_task("qr_scan_task", camera_name="mock_cam")
    final = await qr_task_manager.wait_for_task(task.id)

    assert final.status == TaskStatus.ERROR
    assert "Failed to connect" in (final.result or {}).get("error", "")


@pytest.mark.asyncio
async def test_cleanup_stale_qr_tasks_removes_cancelled_and_limits_errors(monkeypatch, qr_task_manager):
    """Verify cleanup removes stale statuses and keeps only the latest three errors."""

    monkeypatch.setattr(qr_module, "get_task_manager", lambda: qr_task_manager)

    now = datetime.utcnow()
    statuses = [
        (TaskStatus.CANCELLED, -10),
        (TaskStatus.INTERRUPTED, -9),
        (TaskStatus.ERROR, -8),
        (TaskStatus.ERROR, -7),
        (TaskStatus.ERROR, -6),
        (TaskStatus.ERROR, -5),
    ]

    for status, offset in statuses:
        task = Task(name="qr_scan_task", task_type="qr_scan_task", status=status)
        task.created_at = now + timedelta(seconds=offset)
        qr_task_manager._tasks[task.id] = task

    await qr_module._cleanup_stale_qr_tasks()

    remaining = qr_task_manager.get_all_tasks_info()
    assert all(task.status == TaskStatus.ERROR for task in remaining)
    assert len(remaining) == 3
