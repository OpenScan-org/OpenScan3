# OpenScan3 WebSockets

This document explains how OpenScan3 exposes realtime updates via WebSockets, why these channels exist, and how other components can interact with them.

## Why WebSockets?

- Reduce polling pressure on the REST API for frequently changing data (tasks, device telemetry).
- Provide real-time feedback for the UI when tasks start, update progress, finish, or hardware changes state.
- Offer a shared event layer that additional domains can reuse without introducing more REST polling.

## Endpoint Layout

- **Router module:** `openscan/routers/websocket.py`
- **Base path:** `/ws`
- **Task stream endpoint:** `/ws/tasks`
- **Device stream endpoint:** `/ws/device`
- The router keeps WebSocket logic independent from existing HTTP routers.

Under the hood a shared `WebSocketHub` manages namespaces (`tasks`, `device`, …). Each endpoint simply registers a client with the hub, while publishers push JSON payloads into the appropriate namespace.

## Connection Lifecycle

1. Client opens `GET ws://<host>/ws/<namespace>` (e.g. `tasks`, `device`).
2. Server accepts the connection, registers it with the `WebSocketHub` under the target namespace.
3. While connected, the server pushes JSON events whenever publishers emit updates.
4. Every 30 seconds the router sends a heartbeat `{"type": "ping"}` if the connection is otherwise idle.
5. When the client disconnects (clean or abrupt), the hub removes the socket from that namespace. Stale sockets are pruned automatically if a send fails.

### WebSocketHub Responsibilities

- Accept and keep track of active WebSocket clients per namespace.
- Broadcast JSON payloads to all clients, pruning stale connections automatically when send operations fail.
- Provide a simple async API (`broadcast_json(namespace, message)`) that other modules can await to push events.
- Work with the router’s heartbeat loop to keep idle connections alive.

## Message Formats

### Task Events

```json
{
  "type": "task.update",
  "task": {
    "id": "a0d4...",
    "name": "scan_task",
    "status": "running",
    "progress": {
      "current": 3,
      "total": 10,
      "message": "Capturing photo 3/10"
    }
  }
}
```

Key points:
- `type` allows distinguishing between future event kinds (e.g., `task.deleted`, `task.error`).
- `task` payload mirrors the existing `Task` model JSON representation.
- Structure stays consistent with REST responses to minimise client work.

### Device Status Events

```json
{
  "type": "device.status",
  "device": {
    "name": "OpenScan Mini",
    "model": "mini",
    "shield": "greenshield",
    "initialized": true,
    "cameras": {
      "main": {
        "busy": false,
        "settings": { "iso": 200, "jpeg_quality": 92 }
      }
    },
    "motors": {
      "turntable": {
        "busy": true,
        "angle": 180.0,
        "settings": { "speed": 120 }
      }
    },
    "lights": {
      "ring": {
        "is_on": true,
        "settings": { "pins": [5, 6, 13] }
      }
    }
  },
  "changed": [
    "motors.turntable.busy",
    "motors.turntable.angle"
  ]
}
```

Key points:
- `device` contains the full snapshot returned by `DeviceStatusResponse`.
- `changed` is optional and, when present, lists dotted paths that triggered the broadcast (e.g., busy state changes, settings updates).
- Clients can optimistically patch existing state using `changed`, or simply replace their local cache with the provided snapshot.

## Client Integration Notes

- Browsers: use the native [`WebSocket`](https://developer.mozilla.org/en-US/docs/Web/API/WebSocket) API.
- Python CLI or services: use libraries such as [`websockets`](https://websockets.readthedocs.io/) or `aiohttp`.
- Handle reconnects gracefully: if the connection drops, retry after a short delay and resync via REST (`/projects/{id}/tasks`, `/device/info`, …) if necessary.
- Expect periodic `{"type": "ping"}` heartbeats every 30 seconds when idle. Clients may reply with `pong` or simply ignore them.
- For the device stream, some events include only metadata about what changed in `changed`—be prepared to interpret or ignore the list.

## Testing Strategy

- Use FastAPI's WebSocket test client to simulate subscribers during unit tests.
- Ensure broadcast order respects FIFO semantics (especially for exclusive tasks).
- Add regression tests that start a task, expect at least `task.update` events for status transitions, and verify connection cleanup.
- Add coverage for hardware-triggered events (settings updates, busy toggles, initial device snapshot after boot).
- Current coverage: `tests/routers/test_websocket_router.py` verifies that published task events reach connected clients; device tests are planned.

## Implementation Checklist

- [x] Create `WebSocketHub` to track connections per namespace and broadcast events.
- [x] Add `/ws/tasks` endpoint that wires into the hub.
- [x] Introduce a task event publisher that the `TaskManager` can call.
- [x] Emit events for lifecycle hooks (creation, progress, completion, cancellation, errors).
- [x] Add `/ws/device` endpoint for device status updates.
- [x] Introduce a device event publisher and hook it into relevant controller callbacks (settings, busy state, initialization).
- [ ] Extend automated tests to cover device event broadcasts.
- [x] Document final API details and provide example client snippets.

## Example Client (Python)

```python
import asyncio
import json
import websockets

async def listen_to_tasks():
    async with websockets.connect("ws://localhost:8000/latest/ws/tasks") as ws:
        async for message in ws:
            payload = json.loads(message)
            if payload.get("type") == "ping":
                await ws.send(json.dumps({"type": "pong"}))
                continue

            task = payload.get("task", {})
            print(f"Task {task.get('id')} -> {payload.get('type')} ({task.get('status')})")

if __name__ == "__main__":
    asyncio.run(listen_to_tasks())
```

## Example Device Client (Python)

```python
import asyncio
import json
import websockets

async def listen_to_device():
    async with websockets.connect("ws://localhost:8000/latest/ws/device") as ws:
        async for message in ws:
            payload = json.loads(message)
            if payload.get("type") == "ping":
                continue

            changed = payload.get("changed") or []
            print(f"Device update ({', '.join(changed) or 'full snapshot'})")

if __name__ == "__main__":
    asyncio.run(listen_to_device())
