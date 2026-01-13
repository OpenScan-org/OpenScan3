"""Tests for the WebSocket task stream router."""

from __future__ import annotations

import anyio
from fastapi.testclient import TestClient

from openscan_firmware.controllers.services.tasks.task_events import (
    TaskEventType,
    task_event_publisher,
)
from openscan_firmware.controllers.services.device_events import device_event_publisher
from openscan_firmware.main import app
from openscan_firmware.models.task import Task


def test_task_updates_are_broadcast_to_subscribers() -> None:
    """Ensure TaskManager events reach connected WebSocket clients."""
    with TestClient(app) as client:
        with client.websocket_connect("/latest/ws/tasks") as websocket:
            task = Task(name="demo_task", task_type="demo_task")

            anyio.run(task_event_publisher.publish, task, TaskEventType.UPDATE)

            message = websocket.receive_json()
            assert message["type"] == TaskEventType.UPDATE.value
            assert message["task"]["id"] == task.id
            assert message["task"]["name"] == task.name


def test_device_updates_are_broadcast_to_subscribers() -> None:
    """Ensure device status events reach connected WebSocket clients."""
    sample_status = {
        "name": "Test Scanner",
        "model": "mini",
        "shield": "greenshield",
        "initialized": True,
        "cameras": {"main": {"busy": False, "settings": {}}},
        "motors": {"turntable": {"busy": True, "angle": 90, "settings": {}}},
        "lights": {"ring": {"is_on": True, "settings": {}}},
    }

    original_provider = device_event_publisher._status_provider
    device_event_publisher._status_provider = lambda: sample_status
    try:
        with TestClient(app) as client:
            with client.websocket_connect("/latest/ws/device") as websocket:
                anyio.run(
                    device_event_publisher.publish_status,
                    ["motors.turntable.busy"],
                )

                message = websocket.receive_json()
                assert message["type"] == "device.status"
                assert message["device"] == sample_status
                assert message["changed"] == ["motors.turntable.busy"]
    finally:
        device_event_publisher._status_provider = original_provider
