"""Utilities for publishing device status updates via WebSockets."""

from __future__ import annotations

import asyncio
from enum import Enum
from typing import Any, Callable, Sequence

from pydantic import BaseModel

from openscan.routers.websocket import WebSocketHub, get_websocket_hub


class DeviceEventType(str, Enum):
    """Supported device event types."""

    STATUS = "device.status"


class DeviceEventMessage(BaseModel):
    """Wire format for device events sent to WebSocket clients."""

    type: DeviceEventType
    device: dict[str, Any]
    changed: list[str] | None = None


class DeviceEventPublisher:
    """Bridge between device state updates and the WebSocket broadcast layer."""

    def __init__(
        self,
        hub_getter: Callable[[], WebSocketHub] | None = None,
        status_provider: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self._hub_getter = hub_getter or get_websocket_hub
        self._status_provider = status_provider

    def _resolve_status(self) -> dict[str, Any]:
        if self._status_provider is not None:
            return self._status_provider()

        from openscan.controllers import device as device_controller  # Lazy import to avoid circular deps

        return device_controller.get_device_info()

    async def publish_status(self, changed: Sequence[str] | None = None) -> None:
        """Broadcast the current device status to all subscribed WebSocket clients."""
        hub: WebSocketHub = self._hub_getter()
        status_payload = self._resolve_status()
        message = DeviceEventMessage(
            type=DeviceEventType.STATUS,
            device=status_payload,
            changed=list(changed) if changed else None,
        )
        await hub.broadcast_json("device", message.model_dump(mode="json"))


device_event_publisher = DeviceEventPublisher()


def schedule_device_status_broadcast(changed: Sequence[str] | None = None) -> None:
    """Schedule a device status update event for WebSocket subscribers."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(device_event_publisher.publish_status(changed=changed))
    else:
        loop.create_task(device_event_publisher.publish_status(changed=changed))


def notify_busy_change(component: str, name: str) -> None:
    """Publish a device status event indicating a busy-state change."""
    schedule_device_status_broadcast([f"{component}.{name}.busy"])
