"""WebSocket endpoints for pushing realtime updates.

This module exposes a namespace-aware hub that currently serves two independent
channels: one for TaskManager events and another for device status updates.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Set

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

router = APIRouter(prefix="/ws", tags=["websockets"])

logger = logging.getLogger(__name__)

class WebSocketHub:
    """Track active WebSockets per namespace and handle broadcasts."""

    def __init__(self) -> None:
        self._connections: Dict[str, Set[WebSocket]] = {}

    async def register(self, namespace: str, websocket: WebSocket) -> None:
        """Accept the connection and add it to the namespace pool."""
        await websocket.accept()
        self._connections.setdefault(namespace, set()).add(websocket)
        logger.debug(f"Registered WebSocket for namespace {namespace}")

    def unregister(self, namespace: str, websocket: WebSocket) -> None:
        """Remove a WebSocket from the given namespace pool."""
        if namespace not in self._connections:
            return

        namespace_connections = self._connections[namespace]
        namespace_connections.discard(websocket)
        if not namespace_connections:
            self._connections.pop(namespace, None)

        logger.debug(f"Unregistered WebSocket for namespace {namespace}")

    async def broadcast_json(self, namespace: str, message: dict[str, Any]) -> None:
        """Send a JSON message to all clients registered for a namespace."""
        connections = list(self._connections.get(namespace, ()))
        if not connections:
            return

        stale_connections: list[WebSocket] = []
        for connection in connections:
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.error(f"Failed to send message to WebSocket: {e}")
                stale_connections.append(connection)

        for stale in stale_connections:
            self.unregister(namespace, stale)


_websocket_hub = WebSocketHub()


def get_websocket_hub() -> WebSocketHub:
    """FastAPI dependency that returns the shared WebSocketHub instance."""
    return _websocket_hub


HEARTBEAT_INTERVAL = 30.0


@router.websocket("/tasks")
async def task_stream_endpoint(
    websocket: WebSocket,
    hub: WebSocketHub = Depends(get_websocket_hub),
) -> None:
    """Stream TaskManager updates to connected clients."""
    await hub.register("tasks", websocket)
    try:
        while True:
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=HEARTBEAT_INTERVAL)
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "ping"})
    except WebSocketDisconnect:
        pass
    finally:
        hub.unregister("tasks", websocket)


@router.websocket("/device")
async def device_stream_endpoint(
    websocket: WebSocket,
    hub: WebSocketHub = Depends(get_websocket_hub),
) -> None:
    """Stream device status updates to connected clients."""
    await hub.register("device", websocket)
    try:
        while True:
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=HEARTBEAT_INTERVAL)
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "ping"})
    except WebSocketDisconnect:
        pass
    finally:
        hub.unregister("device", websocket)
