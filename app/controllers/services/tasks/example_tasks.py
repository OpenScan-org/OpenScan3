"""
Example Tasks

This module contains four example tasks: HelloWorldTask, ExclusiveDemoTask, ExampleTaskWithGenerator, and BlockingTask for reference.
"""

import asyncio
import logging
import time
from typing import Any, AsyncGenerator, Optional

from app.controllers.services.tasks.base_task import BaseTask
from app.models.task import TaskProgress

logger = logging.getLogger(__name__)

class HelloWorldBlockingTask(BaseTask):
    """
    A task that demonstrates the use of a blocking (synchronous) `run` method.
    By setting `is_blocking = True`, the TaskManager will execute this in a separate
    thread, preventing it from freezing the application's event loop.
    """
    is_exclusive: bool = False
    is_blocking: bool = True  # Mark this task as blocking

    def run(self, *args: Any, **kwargs: Any) -> Any:
        """
        This is a standard synchronous method (`def`, not `async def`).
        It uses `time.sleep()`, which is a blocking call. Because `is_blocking` is True,
        the TaskManager will handle it correctly.
        """
        duration = kwargs.get("duration", 3)
        logger.info(f"[{self.id}] Starting blocking task for {duration} seconds. This will run in a thread.")
        time.sleep(duration)  # This would block, but it's running in a separate thread.
        logger.info(f"[{self.id}] Blocking task finished.")
        return "Blocking task complete."


class HelloWorldAsyncTask(BaseTask):
    """
    A simple demonstration asynchronous non-blocking task that counts to 10, updating its progress.
    The task is not exclusive and can run concurrently with other tasks.
    """
    is_exclusive: bool = False # Mark as non-exclusive


    async def run(self, *args: Any, **kwargs: Any) -> Any:
        """
        Runs the demo task.
        It can count steps like before, but can also wait for an asyncio.Event
        if one is passed in the keyword arguments. This makes it versatile for
        different test scenarios.
        """
        wait_for_event: Optional[asyncio.Event] = kwargs.get('wait_for_event')
        if wait_for_event:
            logger.info(f"[{self.id}] Waiting for event...")
            await wait_for_event.wait()
            logger.info(f"[{self.id}] Event received, finishing task.")
            self._task_model.result = "Event-based task finished."
            return "Event-based task finished."

        total_steps = kwargs.get('total_steps', 5)
        delay = kwargs.get('delay', 0.1)  # Use a shorter delay for tests

        self._task_model.progress = TaskProgress(current=0, total=total_steps, message="Starting Hello World Task...")
        await asyncio.sleep(0.01)  # give a small moment for initial state to be processed

        for i in range(1, total_steps + 1):
            await self.wait_for_pause()
            if self.is_cancelled():
                self._task_model.progress.message = "Hello World task cancelled."
                return "Hello World task cancelled by request."

            self._task_model.progress = TaskProgress(current=i, total=total_steps, message=f"Hello World! Step {i} of {total_steps}")
            await asyncio.sleep(delay)

        logger.info(f"[{self.id}] HelloWorldTask finished.")
        final_message = f"Hello World! Completed {total_steps} steps successfully."
        self._task_model.progress = TaskProgress(current=total_steps, total=total_steps, message=final_message)
        self._task_model.result = final_message
        return final_message


class ExclusiveDemoTask(BaseTask):
    """
    A simple demonstration task that runs exclusively.
    Simulates an operation that requires sole access to resources.
    """
    is_exclusive: bool = True  # Mark as exclusive

    async def run(self, duration: float = 1.0):
        """A simple task that just sleeps for a given duration, simulating an exclusive operation."""
        logger.info(f"Starting exclusive task '{self.id}' for {duration}s.")
        self._task_model.progress = TaskProgress(current=0, total=1, message="Starting exclusive lock")

        # Simulate work by sleeping for the total duration
        await asyncio.sleep(duration)

        self._task_model.progress = TaskProgress(current=1, total=1, message="Finished exclusive lock")
        logger.info(f"Finished exclusive task '{self.id}'.")
        return {"status": "completed", "duration": duration}


class ExampleTaskWithGenerator(BaseTask):
    """
    A task that demonstrates streaming progress via an async generator and
    supports being resumed from its last known progress point.
    """
    is_exclusive = False
    is_blocking = False

    async def run(self, total_steps: int = 10, interval: float = 0.5) -> AsyncGenerator[TaskProgress, None]:
        """
        Runs the streaming task, yielding `TaskProgress` objects.

        If the task is restarted, it will resume from the last completed step.

        Args:
            *args: Expects one integer argument for the total number of steps.
            **kwargs: Can take `total_steps` and `interval` for testing.
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
                # The TaskManager will handle the status change.
                # We just need to stop the generator.
                logger.info(f"Task {self.name} ({self.id}) stopping due to cancellation.")
                return

            await asyncio.sleep(interval)  # Simulate work
            yield TaskProgress(
                current=i + 1,
                total=steps,
                message=f"Step {i + 1} of {steps} complete."
            )

        # The task is considered complete when the generator finishes.
        # The TaskManager sets the final status.
        # If a result is needed, it should be set on the model directly:
        # self._task_model.result = "All steps finished."
        logger.info(f"[{self.id}] ExampleTaskWithGenerator finished.")

        self._task_model.result = f"Generator task completed after {total_steps} steps."


class FailingTask(BaseTask):
    is_exclusive = False
    is_blocking = False

    async def run(self, error_message: str = "This task was designed to fail."):
        """This task simply raises an exception to test error handling."""
        await asyncio.sleep(0.01)  # Simulate a tiny bit of work
        raise ValueError(error_message)
