"""Utilities for publishing TaskManager lifecycle events."""

from __future__ import annotations

from enum import Enum
from typing import Any, Callable, TYPE_CHECKING

from pydantic import BaseModel

from openscan.models.task import Task
from openscan.routers.websocket import WebSocketHub, get_websocket_hub

if TYPE_CHECKING:
    from openscan.routers.websocket import WebSocketHub as WebSocketHubType


class TaskEventType(str, Enum):
    """Supported task event types."""

    UPDATE = "task.update"


class TaskEventPublisher:
    """Bridge between TaskManager events and WebSocket broadcast layer."""

    def __init__(self, hub_getter: Callable[[], "WebSocketHubType"] | None = None) -> None:
        self._hub_getter = hub_getter or get_websocket_hub

    async def publish(self, task: Task, event_type: TaskEventType = TaskEventType.UPDATE) -> None:
        hub: WebSocketHub = self._hub_getter()
        event = TaskEventMessage.from_task(task, event_type)
        await hub.broadcast_json("tasks", event.model_dump(mode="json"))


task_event_publisher = TaskEventPublisher()


class TaskEventMessage(BaseModel):
    """Wire format for task events sent to WebSocket clients."""

    type: TaskEventType
    task: dict[str, Any]

    @classmethod
    def from_task(cls, task: Task, event_type: TaskEventType) -> "TaskEventMessage":
        sanitized_task = task.model_dump(
            mode="json",
            exclude={"run_args", "run_kwargs"},
            warnings="none",
        )
        return cls(type=event_type, task=sanitized_task)
