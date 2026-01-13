"""
Autodiscovered example tasks for OpenScan3.

These classes are safe to import (no hardware initialization at import time)
and carry explicit task_name values with the required `_task` suffix.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, AsyncGenerator, Optional

from openscan_firmware.controllers.services.tasks.base_task import BaseTask
from openscan_firmware.models.task import TaskProgress

logger = logging.getLogger(__name__)


class HelloWorldBlockingTask(BaseTask):
    """Demonstrates a blocking (synchronous) task.

    The TaskManager will run this in a thread pool since `is_blocking=True`.
    """

    task_name = "hello_world_blocking_task"
    task_category = "example"
    is_exclusive: bool = False
    is_blocking: bool = True

    def run(self, *args: Any, **kwargs: Any) -> Any:
        """Run the blocking example task.

        Args:
            *args: Unused.
            **kwargs: May contain `duration`.

        Returns:
            A simple completion string.
        """
        duration = kwargs.get("duration", 3)
        logger.info(f"[{self.id}] Starting blocking task for {duration} seconds. This will run in a thread.")
        time.sleep(duration)
        logger.info(f"[{self.id}] Blocking task finished.")
        return "Blocking task complete."


class HelloWorldAsyncTask(BaseTask):
    """Demonstrates an asynchronous non-blocking task with progress updates."""

    task_name = "hello_world_async_task"
    task_category = "example"
    is_exclusive: bool = False

    async def run(self, *args: Any, **kwargs: Any) -> Any:
        """Run the async example task.

        Args:
            *args: Unused.
            **kwargs: `wait_for_event`, `total_steps`, `delay`.

        Returns:
            Final message string when finished.
        """
        wait_for_event: Optional[asyncio.Event] = kwargs.get('wait_for_event')
        if wait_for_event:
            logger.info(f"[{self.id}] Waiting for event...")
            await wait_for_event.wait()
            logger.info(f"[{self.id}] Event received, finishing task.")
            self._task_model.result = "Event-based task finished."
            return "Event-based task finished."

        total_steps = kwargs.get('total_steps', 5)
        delay = kwargs.get('delay', 0.1)

        self._task_model.progress = TaskProgress(current=0, total=total_steps, message="Starting Hello World Task...")
        await asyncio.sleep(0.01)

        for i in range(1, total_steps + 1):
            await self.wait_for_pause()
            if self.is_cancelled():
                self._task_model.progress.message = "Hello World task cancelled."
                return "Hello World task cancelled by request."

            self._task_model.progress = TaskProgress(current=i, total=total_steps, message=f"Hello World! Step {i} of {total_steps}")
            logger.info(f"[{self.id}] Hello World! Step {i} of {total_steps}")
            await asyncio.sleep(delay)

        logger.info(f"[{self.id}] HelloWorldTask finished.")
        final_message = f"Hello World! Completed {total_steps} steps successfully."
        self._task_model.progress = TaskProgress(current=total_steps, total=total_steps, message=final_message)
        self._task_model.result = final_message
        return final_message


class ExclusiveDemoTask(BaseTask):
    """Demonstrates an exclusive async task."""

    task_name = "exclusive_demo_task"
    task_category = "example"
    is_exclusive: bool = True

    async def run(self, duration: float = 1.0):
        """Sleep for a given duration to simulate exclusive work.

        Args:
            duration: Duration in seconds to sleep.
        """
        logger.info(f"Starting exclusive task '{self.id}' for {duration}s.")
        self._task_model.progress = TaskProgress(current=0, total=1, message="Starting exclusive lock")
        await asyncio.sleep(duration)
        self._task_model.progress = TaskProgress(current=1, total=1, message="Finished exclusive lock")
        logger.info(f"Finished exclusive task '{self.id}'.")
        return {"status": "completed", "duration": duration}


class ExampleTaskWithGenerator(BaseTask):
    """Demonstrates streaming progress via async generator with resume support."""

    task_name = "generator_task"
    task_category = "example"
    is_exclusive = False
    is_blocking = False

    async def run(self, total_steps: int = 10, interval: float = 0.5) -> AsyncGenerator[TaskProgress, None]:
        """Run the streaming generator task.

        Args:
            total_steps: The number of steps to complete.
            interval: Sleep interval per step.

        Yields:
            TaskProgress updates.
        """
        steps = total_steps
        start_step = self._task_model.progress.current

        if start_step >= steps:
            yield TaskProgress(current=steps, total=steps, message="Task already completed.")
            return

        yield TaskProgress(current=start_step, total=steps, message=f"Starting/Resuming from step {start_step}.")

        for i in range(int(start_step), steps):
            await self.wait_for_pause()
            if self.is_cancelled():
                logger.info(f"Task {self.name} ({self.id}) stopping due to cancellation.")
                return

            await asyncio.sleep(interval)
            yield TaskProgress(
                current=i + 1,
                total=steps,
                message=f"Step {i + 1} of {steps} complete."
            )

        logger.info(f"[{self.id}] ExampleTaskWithGenerator finished.")
        self._task_model.result = f"Generator task completed after {total_steps} steps."


class FailingTask(BaseTask):
    """A task that raises an exception to test error handling."""

    task_name = "failing_task"
    task_category = "example"
    is_exclusive = False
    is_blocking = False

    async def run(self, error_message: str = "This task was designed to fail."):
        """Raise an exception after a tiny delay.

        Args:
            error_message: The message to raise.
        """
        await asyncio.sleep(0.01)
        raise ValueError(error_message)
