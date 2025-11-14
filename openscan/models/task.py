import uuid
from datetime import datetime
from enum import Enum
from typing import Optional, Any

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    """Enum for task status"""
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ERROR = "error"
    INTERRUPTED = "interrupted"

class TaskProgress(BaseModel):
    """Model for task progress."""
    current: float = Field(0.0, description="The current step or value of progress (e.g., files processed).")
    total: float = Field(0.0, description="The total number of steps or value for completion (e.g., total files).")
    message: str = ""

class Task(BaseModel):
    """Represents a background task."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    task_type: str
    is_exclusive: bool = Field(False, description="Whether this task is exclusive and should not run concurrently")
    is_blocking: bool = Field(False, description="Whether this task is blocking and should run in a separate thread")
    status: TaskStatus = TaskStatus.PENDING
    progress: TaskProgress = Field(default_factory=TaskProgress)
    result: Optional[Any] = None
    error: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.now)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    run_args: tuple = Field(default_factory=tuple, description="Positional arguments the task was started with.")
    run_kwargs: dict = Field(default_factory=dict, description="Keyword arguments the task was started with.")
