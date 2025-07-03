"""
TaskManager

Manages the lifecycle and concurrency of background tasks in the application.

This module provides a singleton `TaskManager` responsible for:
1.  **Centralized Task Management**: A single point of control for all foreground and background
    tasks, ensuring consistent state management and execution.

2.  **Concurrency and Scheduling**:
    - **Exclusive Tasks**: Tasks marked as `is_exclusive` have the highest priority.
      An exclusive task will only start if no other tasks are running. While an
      exclusive task is running or pending, no other tasks will be started.
    - **Async Tasks**: Non-exclusive, non-blocking (`is_blocking=False`) tasks run
      concurrently up to a configurable limit (`max_concurrent_non_exclusive_tasks`).
    - **Blocking Tasks**: Non-exclusive, blocking (`is_blocking=True`) tasks are
      executed in a separate thread pool. They are not limited by the async
      concurrency limit, allowing CPU-bound work without blocking the event loop.

3.  **Task Queueing**: Tasks that cannot be started immediately are placed in a
    FIFO queue. The scheduling logic ensures that pending exclusive tasks block
    the queue for non-exclusive tasks, preserving priority.

4.  **Lifecycle Control**: Provides an API to create, run, cancel, pause, and
    resume tasks.

5.  **Task Persistence**: Tasks are persisted to JSON files in the storage directory
    for future runs. If a task is successfully completed, it's cleaned up. Interrupted
    or failed tasks are not deleted and can be resumed.

The `task_manager` instance is the singleton to be used across the application.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import pathlib
import time
from datetime import datetime
from typing import Any, Type, Coroutine, AsyncGenerator
import functools

from pydantic import ValidationError
from pydantic_core import PydanticSerializationError

from app.controllers.services.tasks.base_task import BaseTask
from app.models.task import Task, TaskStatus, TaskProgress

logger = logging.getLogger(__name__)

# Configuration for task concurrency
MAX_CONCURRENT_NON_EXCLUSIVE_TASKS = 3
TASKS_STORAGE_PATH = pathlib.Path("data/tasks")


class TaskManager:
    """
    Manages the lifecycle of background tasks.

    This class is a singleton that handles the registration, creation, execution,
    and monitoring of all background tasks within the application.
    It supports exclusive tasks and a concurrency limit for non-exclusive tasks.
    """
    _instance = None

    def __new__(cls) -> TaskManager:
        if cls._instance is None:
            cls._instance = super(TaskManager, cls).__new__(cls)
            cls._instance._task_registry: dict[str, Type[BaseTask]] = {}
            cls._instance._tasks: dict[str, Task] = {}  # Stores task models
            cls._instance._running_task_instances: dict[str, BaseTask] = {}  # Stores running BaseTask instances
            cls._instance._running_async_tasks: dict[str, asyncio.Task[Any]] = {}  # Stores asyncio task handles for non-blocking tasks
            cls._instance._running_blocking_tasks: dict[str, asyncio.Task[Any]] = {}  # Stores asyncio task handles for blocking tasks
            cls._instance._pending_tasks: asyncio.Queue[tuple[BaseTask, tuple, dict]] = asyncio.Queue()
            cls._instance._active_exclusive_task_id: str | None = None
            cls._instance._queue_processing_lock = asyncio.Lock()

            cls._instance.max_concurrent_non_exclusive_tasks = MAX_CONCURRENT_NON_EXCLUSIVE_TASKS

            # Persistence Setup
            cls._instance._tasks_storage_path = TASKS_STORAGE_PATH
            os.makedirs(cls._instance._tasks_storage_path, exist_ok=True)
            # The loading of tasks is now deferred to an explicit call to `restore_tasks_from_persistence`

        return cls._instance

    def restore_tasks_from_persistence(self):
        """Loads all persisted task JSON files from the storage directory.
        This should be called during application startup, after all task types have been registered.

        If a file is corrupt or invalid, it's skipped and a warning is logged.
        Tasks that were successfully completed in a previous run are cleaned up.
        """
        logger.info(f"Restoring persisted tasks from {self._tasks_storage_path}...")

        # Ensure the storage directory exists to prevent errors during testing or first run.
        os.makedirs(self._tasks_storage_path, exist_ok=True)

        loaded_count = 0
        interrupted_count = 0
        cleaned_count = 0
        for filename in os.listdir(self._tasks_storage_path):
            if filename.endswith(".json"):
                file_path = self._tasks_storage_path / filename
                try:
                    with open(file_path, 'r') as f:
                        task_data = json.load(f)

                    # Auto-cleanup for successfully completed tasks
                    if task_data.get('status') == TaskStatus.COMPLETED.value:
                        logger.info(f"Cleaning up successfully completed task file: {filename}")
                        os.remove(file_path)
                        cleaned_count += 1
                        continue

                    task_model = Task.model_validate(task_data)

                    # Check if the task type is registered. If not, mark as FAILED.
                    if task_model.task_type not in self._task_registry:
                        task_model.status = TaskStatus.ERROR
                        task_model.error = f"Task type '{task_model.task_type}' is not registered. Cannot restore."
                        logger.error(f"Failed to load task {task_model.id}: {task_model.error}")
                        self._save_task_state(task_model)  # Persist the FAILED state
                        self._tasks[task_model.id] = task_model
                        loaded_count += 1
                        continue

                    # Reset state for tasks that were running when the app was last closed
                    if task_model.status in [TaskStatus.RUNNING, TaskStatus.PAUSED]:
                        task_model.status = TaskStatus.INTERRUPTED
                        task_model.error = "Task was interrupted by application shutdown."
                        logger.warning(f"Task '{task_model.name}' ({task_model.id}) was interrupted. Set to INTERRUPTED.")
                        self._save_task_state(task_model)  # Persist the new state
                        interrupted_count += 1

                    self._tasks[task_model.id] = task_model
                    loaded_count += 1
                except (json.JSONDecodeError, ValidationError, IOError) as e:
                    logger.warning(f"Could not load or process task file '{filename}': {e}")
                    continue
        if loaded_count > 0 or cleaned_count > 0 or interrupted_count > 0:
            logger.info(
                f"Task loading complete. Loaded: {loaded_count}, Interrupted: {interrupted_count}, Cleaned up: {cleaned_count}."
            )

    def _save_task_state(self, task_model: Task):
        """Saves a single task model to a JSON file, handling non-serializable args."""
        file_path = self._tasks_storage_path / f"{task_model.id}.json"
        json_string: str
        try:
            # Default serialization: try to save everything
            json_string = task_model.model_dump_json(indent=2)
        except PydanticSerializationError as e:
            logger.debug(f"Failed to serialize task {task_model.id} for persistence: {e}")
            logger.warning(
                f"Task {task_model.id} ({task_model.name}) has non-serializable arguments. "
                f"It will not be restartable after an application shutdown."
            )
            # Fallback: serialize without the problematic fields
            json_string = task_model.model_dump_json(
                indent=2,
                exclude={'run_args', 'run_kwargs'}
            )

        try:
            with open(file_path, 'w') as f:
                f.write(json_string)
            logger.debug(f"Persisted state for task {task_model.id} to {file_path}")
        except IOError as e:
            logger.error(f"Failed to save task state for {task_model.id}: {e}", exc_info=True)

    def _delete_task_state(self, task_id: str):
        """Deletes the JSON file for a given task."""
        file_path = self._tasks_storage_path / f"{task_id}.json"
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                logger.debug(f"Deleted persisted state for task {task_id}.")
        except IOError as e:
            logger.error(f"Failed to delete task state for {task_id}: {e}", exc_info=True)

    def register_task(self, name: str, task_class: Type[BaseTask]) -> None:
        """
        Registers a new task type.
        This is necessary before the task can be created and run.
        The task class should inherit from BaseTask and have an `is_exclusive` attribute.

        Args:
            name: The name to identify the task type.
            task_class: The class implementing the task logic, inheriting from BaseTask.
                      The class should have an `is_exclusive: bool` attribute.
        """
        if name in self._task_registry:
            logger.warning(f"Task '{name}' is already registered. Overwriting.")

        self._task_registry[name] = task_class
        logger.info(f"Task '{name}' (exclusive: {getattr(task_class, 'is_exclusive', False)}) registered successfully.")

    def get_task_info(self, task_id: str) -> Task | None:
        """Retrieves the data model for a specific task."""
        return self._tasks.get(task_id)

    def get_all_tasks_info(self) -> list[Task]:
        """Retrieves the data models for all tasks."""
        return list(self._tasks.values())

    async def delete_task(self, task_id: str) -> None:
        """
        Deletes a task, removing it from memory and deleting its state from disk.

        Only tasks in a terminal state (COMPLETED, CANCELLED, ERROR, INTERRUPTED)
        can be deleted. Attempting to delete a running, paused, or pending task
        will raise an error.

        Args:
            task_id: The ID of the task to delete.

        Raises:
            ValueError: If the task does not exist or is in a non-terminal state.
        """
        task_model = self.get_task_info(task_id)

        if not task_model:
            # If not in memory, it might be a completed task already cleaned up.
            # We still try to delete the file just in case.
            self._delete_task_state(task_id)
            logger.info(f"Attempted to delete task '{task_id}' which was not in memory. Ensured file is removed.")
            return

        if task_model.status in [TaskStatus.RUNNING, TaskStatus.PAUSED, TaskStatus.PENDING]:
            raise ValueError(
                f"Cannot delete task '{task_id}' while it is in status '{task_model.status.value}'. "
                f"Cancel the task first."
            )

        # Remove from in-memory dictionary
        if task_id in self._tasks:
            del self._tasks[task_id]
            logger.info(f"Removed task '{task_id}' from memory.")

        # Delete the persisted file
        self._delete_task_state(task_id)

    async def wait_for_task(self, task_id: str, timeout: float = 20.0) -> Task:
        """
        Waits for a task to reach a terminal state (Completed, Error, Cancelled).

        Args:
            task_id: The ID of the task to wait for.
            timeout: The maximum time to wait in seconds.

        Returns:
            The final task model.

        Raises:
            asyncio.TimeoutError: If the task does not complete within the timeout.
            ValueError: If the task_id is not found.
        """
        if task_id not in self._tasks:
            raise ValueError(f"Task with ID {task_id} not found.")

        start_time = time.time()
        while time.time() - start_time < timeout:
            task_model = self.get_task_info(task_id)
            if task_model.status in [TaskStatus.COMPLETED, TaskStatus.ERROR, TaskStatus.CANCELLED]:
                # Give the event loop one last cycle to process any final updates in the wrapper
                await asyncio.sleep(0)
                return self.get_task_info(task_id)
            await asyncio.sleep(0.05)  # Poll every 50ms

        raise asyncio.TimeoutError(f"Timeout waiting for task {task_id} to complete.")

    def _has_pending_exclusive_task(self) -> bool:
        """Checks if any task in the pending queue is exclusive."""
        # Note: Accessing the protected _queue member is a pragmatic way to inspect
        # the queue content without consuming items, which asyncio.Queue doesn't natively support.
        for task_instance, _, _ in self._pending_tasks._queue:
            if self._tasks[task_instance.id].is_exclusive:
                logger.debug(f"Exclusive task '{self._tasks[task_instance.id].name}' is pending.")
                return True
        return False

    def _can_run_task(self, task_is_exclusive: bool, task_is_blocking: bool) -> bool:
        """
        Checks if a task can run based on current active tasks and concurrency limits.
        NOTE: This check does NOT consider the state of the pending queue.
        """
        # 1. An active exclusive task blocks everything.
        if self._active_exclusive_task_id:
            logger.debug(f"Cannot run task: An exclusive task '{self._active_exclusive_task_id}' is running.")
            return False

        all_running_tasks_count = len(self._running_async_tasks) + len(self._running_blocking_tasks)

        # 2. A new exclusive task can only run if nothing else is running.
        if task_is_exclusive:
            return all_running_tasks_count == 0

        # 3. A new blocking, non-exclusive task can always run if no exclusive task is active.
        if task_is_blocking:
            return True

        # 4. A new async, non-exclusive task is subject to the async concurrency limit.
        return len(self._running_async_tasks) < self.max_concurrent_non_exclusive_tasks

    async def create_and_run_task(self, task_name: str, *args: Any, **kwargs: Any) -> Task:
        """
        Creates a new task. If possible, it starts the task immediately.
        Otherwise, the task is added to a pending queue.

        Note: The task must be already registered.

        Args:
            task_name: The name of the registered task to run.
            *args: Positional arguments to pass to the task's run method.
            **kwargs: Keyword arguments to pass to the task's run method.

        Returns:
            The data model of the created task.

        Raises:
            ValueError: If the task name is not registered.
        """
        if task_name not in self._task_registry:
            raise ValueError(f"Task '{task_name}' is not registered.")

        task_class = self._task_registry[task_name]
        task_model_is_exclusive = getattr(task_class, 'is_exclusive', False)
        task_is_blocking = getattr(task_class, 'is_blocking', False)

        # Store original args and kwargs for potential restart
        task_model = Task(
            name=task_name,
            task_type=task_name,  # Persist the task type for reconstruction
            is_exclusive=task_model_is_exclusive,
            is_blocking=task_is_blocking,
            run_args=args,
            run_kwargs=kwargs
        )
        task_instance = task_class(task_model)  # BaseTask instance

        self._tasks[task_model.id] = task_model
        self._save_task_state(task_model)  # Persist immediately on creation

        # Scheduling Decision Point:
        # A new non-exclusive task must be queued if an exclusive task is already pending.
        should_queue_due_to_pending_exclusive = not task_model.is_exclusive and self._has_pending_exclusive_task()

        if not should_queue_due_to_pending_exclusive and self._can_run_task(task_model.is_exclusive, task_is_blocking):
            logger.info(f"Starting task '{task_model.name}' ({task_model.id}) immediately.")
            self._start_task_execution(task_instance, *args, **kwargs)
        else:
            if should_queue_due_to_pending_exclusive:
                logger.info(f"Queueing task '{task_model.name}' ({task_model.id}) because an exclusive task is pending.")
            else:
                logger.info(f"Queueing task '{task_model.name}' ({task_model.id}). Conditions not met for immediate start.")
            await self._pending_tasks.put((task_instance, args, kwargs))
            # Task remains in PENDING status by default

        return task_model

    def _start_task_execution(self, task_instance: BaseTask, *args: Any, **kwargs: Any) -> None:
        """Internal helper to actually start a task's execution."""
        task_model = self._tasks[task_instance.id]
        task_model.status = TaskStatus.RUNNING  # Set to running before async task creation
        task_model.started_at = datetime.now()
        self._save_task_state(task_model)  # Persist the RUNNING state immediately

        if task_model.is_exclusive:
            self._active_exclusive_task_id = task_model.id

        self._running_task_instances[task_model.id] = task_instance
        async_task = asyncio.create_task(
            self._run_wrapper(task_instance, *args, **kwargs)
        )

        if task_instance.is_blocking:
            self._running_blocking_tasks[task_model.id] = async_task
        else:
            self._running_async_tasks[task_model.id] = async_task

    async def _run_wrapper(self, task_instance: BaseTask, *args: Any, **kwargs: Any) -> None:
        """
        A wrapper to manage task execution and state updates.
        Handles async tasks (including generators) and synchronous (blocking) tasks.
        """
        task_model = self._tasks[task_instance.id]
        # Status is already set to RUNNING in _start_task_execution

        try:
            if task_instance.is_blocking:
                # Run synchronous, blocking code in a separate thread to avoid blocking the event loop.
                # Note: Pause/Resume/Cancel are not supported for blocking tasks as they run to completion.
                loop = asyncio.get_running_loop()
                func = functools.partial(task_instance.run, *args, **kwargs)
                result = await loop.run_in_executor(None, func)  # `None` uses the default ThreadPoolExecutor
                task_model.result = result
                task_model.status = TaskStatus.COMPLETED
            else:
                # Execute the async task's run method
                run_result = task_instance.run(*args, **kwargs)

                if isinstance(run_result, AsyncGenerator):
                    # Handle async generator for streaming progress.
                    # The generator is expected to ONLY yield `TaskProgress` objects.
                    generator = run_result
                    async for progress_update in generator:
                        await task_instance.wait_for_pause()  # Allow task to pause here
                        if task_instance.is_cancelled():  # Check for cancellation during iteration
                            logger.info(f"Task {task_model.name} ({task_model.id}) cancelled during async generator execution.")
                            task_model.status = TaskStatus.CANCELLED
                            break  # Exit the generator loop

                        # Update progress. It's now required that tasks yield `TaskProgress` objects.
                        task_model.progress = progress_update
                        self._save_task_state(task_model)  # Persist progress immediately

                    if task_model.status not in [TaskStatus.CANCELLED, TaskStatus.ERROR]:
                        task_model.status = TaskStatus.COMPLETED
                        # For generator tasks, the result is not derived from the generator.
                        # The task implementation is responsible for setting a result on the
                        # task model directly if one is needed.
                        if task_model.result is None:
                            logger.debug(f"Generator task {task_model.name} completed without an explicit result.")

                else:
                    # Handle regular async function
                    result = await run_result
                    task_model.result = result
                    task_model.status = TaskStatus.COMPLETED

        except asyncio.CancelledError:
            task_model.status = TaskStatus.CANCELLED
            task_model.error = "Task was cancelled by user."
            logger.warning(f"Task {task_instance.name} ({task_instance.id}) was cancelled.")
        except Exception as e:
            logger.error(f"Task {task_model.name} ({task_model.id}) failed: {e}", exc_info=True)
            task_model.status = TaskStatus.ERROR
            task_model.error = str(e)
        finally:
            task_model.completed_at = datetime.now()

            if task_model.status == TaskStatus.COMPLETED:
                task_model.progress.current = task_model.progress.total  # Mark as 100%

            # Persist the final state of the task
            self._save_task_state(task_model)

            # Pop from the correct dictionary
            if task_instance.is_blocking:
                self._running_blocking_tasks.pop(task_instance.id, None)
            else:
                self._running_async_tasks.pop(task_instance.id, None)

            self._running_task_instances.pop(task_instance.id, None)

            if task_model.is_exclusive and self._active_exclusive_task_id == task_instance.id:
                self._active_exclusive_task_id = None

            asyncio.create_task(self._try_run_pending_tasks())  # Non-blocking attempt to run next task

    async def _try_run_pending_tasks(self) -> None:
        """Attempts to run tasks from the pending queue if conditions allow."""
        # Use a lock to prevent race conditions where multiple tasks finish simultaneously
        # and trigger this method, leading to inconsistent state checks.
        async with self._queue_processing_lock:
            # This method processes the queue in a strict FIFO manner. If the task at the
            # head of the queue cannot run, no other tasks behind it will be considered.
            # This is crucial for ensuring that pending exclusive tasks properly block the queue.

            while not self._pending_tasks.empty():
                # Peek at the head of the queue without removing the item.
                # We access the internal _queue (a deque) for this, as asyncio.Queue has no peek().
                task_instance, args, kwargs = self._pending_tasks._queue[0]
                task_model = self._tasks[task_instance.id]

                if self._can_run_task(task_model.is_exclusive, task_instance.is_blocking):
                    # The task can run, so now we officially remove it from the queue.
                    await self._pending_tasks.get()

                    logger.info(f"Dequeuing and starting pending task '{task_model.name}' ({task_model.id}).")
                    self._start_task_execution(task_instance, *args, **kwargs)

                    # If we just started an exclusive task, we must stop processing the queue.
                    if task_model.is_exclusive:
                        break

                    # If we've hit the concurrency limit for async tasks, stop for now.
                    if not task_instance.is_blocking and len(self._running_async_tasks) >= self.max_concurrent_non_exclusive_tasks:
                        logger.debug(
                            f"Async task concurrency limit ({self.max_concurrent_non_exclusive_tasks}) reached. "
                            f"Pausing queue processing."
                        )
                        break

                    # Continue the loop to see if more tasks can be started.
                else:
                    # The head of the queue cannot run. We must stop and wait for conditions to change.
                    logger.debug(f"Head of queue task '{task_model.name}' cannot run. Halting queue processing.")
                    break

    async def cancel_task(self, task_id: str) -> Task | None:
        """
        Cancels a running or pending task.

        Args:
            task_id: The ID of the task to cancel.

        Returns:
            The updated task model if the task was found, otherwise None.
        """
        if task_id not in self._tasks:
            logger.warning(f"Attempted to cancel non-existent task {task_id}.")
            return None

        task_model = self._tasks[task_id]

        # Case 1: Task is running
        if task_id in self._running_async_tasks or task_id in self._running_blocking_tasks:
            logger.info(f"Cancelling running task {task_model.name} ({task_id}).")
            async_task_handle = self._running_async_tasks.get(task_id) or self._running_blocking_tasks.get(task_id)
            base_task_instance = self._running_task_instances[task_id]

            base_task_instance.cancel()  # Signal BaseTask to stop its work
            async_task_handle.cancel()  # Cancel the asyncio.Task wrapper

            # The _run_wrapper's finally block will handle status updates and cleanup.
            # We can preemptively set status for quicker feedback if desired, but it might be overwritten.
            task_model.status = TaskStatus.CANCELLED
            task_model.error = "Task was cancelled by user."
            self._save_task_state(task_model)
            # Note: completed_at will be set in _run_wrapper
            return task_model

        # Case 2: Task is pending
        # This requires iterating through the queue, removing the task, and re-adding others.
        # This is complex with asyncio.Queue. A simpler approach for now is to mark it
        # and let _try_run_pending_tasks skip it or handle it.
        # Let's try to find and remove it if it's in the queue.
        found_and_removed_from_queue = False
        temp_queue: list[tuple[BaseTask, tuple, dict]] = []
        while not self._pending_tasks.empty():
            pending_task_instance, p_args, p_kwargs = await self._pending_tasks.get()
            if pending_task_instance.id == task_id:
                logger.info(f"Cancelling pending task {task_model.name} ({task_id}) by removing from queue.")
                task_model.status = TaskStatus.CANCELLED
                task_model.error = "Task was cancelled by user."
                task_model.completed_at = datetime.now()
                self._save_task_state(task_model)  # Persist cancellation of pending task
                found_and_removed_from_queue = True
                # Do not re-add this task to the queue
            else:
                temp_queue.append((pending_task_instance, p_args, p_kwargs))

        # Re-add the other pending tasks to the queue
        for item in temp_queue:
            await self._pending_tasks.put(item)

        if found_and_removed_from_queue:
            return task_model

        # Case 3: Task is already completed, failed, or cancelled
        if task_model.status in [TaskStatus.COMPLETED, TaskStatus.ERROR, TaskStatus.CANCELLED]:
            logger.warning(f"Task {task_id} is already in terminal state: {task_model.status}. Cannot cancel.")
            return task_model

        logger.warning(f"Could not determine state for cancelling task {task_id}. Status: {task_model.status}")
        return task_model  # Should not happen if logic is correct

    async def pause_task(self, task_id: str) -> Task | None:
        """
        Pauses a running task.

        This method is the single point of control for pausing a task.
        It sets the task status to PAUSED and signals the task to halt its execution.

        Args:
            task_id: The ID of the task to pause.

        Returns:
            The updated task model if found and paused, otherwise None.
        """
        if task_id not in self._running_task_instances:
            logger.warning(f"Cannot pause task {task_id}: not currently running.")
            return self.get_task_info(task_id)

        task_model = self._tasks[task_id]
        task_instance = self._running_task_instances[task_id]

        if task_model.status != TaskStatus.RUNNING:
            logger.warning(f"Cannot pause task {task_id}: status is '{task_model.status}', not 'running'.")
            return task_model

        task_model.status = TaskStatus.PAUSED
        task_instance.pause()  # Signal the task to pause

        logger.info(f"Task {task_model.name} ({task_id}) has been paused.")
        task_model.progress.message = "Task paused."
        self._save_task_state(task_model)
        return task_model

    async def resume_task(self, task_id: str) -> Task | None:
        """
        Resumes a paused task.

        This method is the single point of control for resuming a task.
        It sets the task status back to RUNNING and signals the task to continue.

        Args:
            task_id: The ID of the task to resume.

        Returns:
            The updated task model if found and resumed, otherwise None.
        """
        if task_id not in self._running_task_instances:
            logger.warning(f"Cannot resume task {task_id}: not currently running or does not exist.")
            return self.get_task_info(task_id)

        task_model = self._tasks[task_id]
        task_instance = self._running_task_instances[task_id]

        if task_model.status != TaskStatus.PAUSED:
            logger.warning(f"Cannot resume task {task_id}: status is '{task_model.status}', not 'paused'.")
            return task_model

        task_model.status = TaskStatus.RUNNING
        task_instance.resume()  # Signal the task to resume

        logger.info(f"Task {task_model.name} ({task_id}) has been resumed.")
        task_model.progress.message = "Task resumed."
        self._save_task_state(task_model)
        return task_model

    async def restart_task(self, task_id: str) -> Task | None:
        """
        Restarts a task that is in a CANCELLED or ERROR state.

        The task will be re-queued or run immediately with its original arguments.
        The task's implementation is responsible for handling the continuation
        from its last known progress.

        Args:
            task_id: The ID of the task to restart.

        Returns:
            The updated task model if found, otherwise None.
        """
        task_model = self.get_task_info(task_id)
        if not task_model:
            logger.warning(f"Attempted to restart non-existent task {task_id}.")
            return None

        if task_model.status not in [TaskStatus.CANCELLED, TaskStatus.ERROR, TaskStatus.INTERRUPTED]:
            logger.warning(f"Task {task_id} is not in a restartable state (status: {task_model.status}).")
            return task_model

        # Reset task state for restart, but keep progress and original creation date
        task_model.status = TaskStatus.PENDING
        task_model.started_at = None
        task_model.completed_at = None
        task_model.error = None
        task_model.result = None
        task_model.progress = TaskProgress()  # Resets progress

        self._save_task_state(task_model)  # Persist the reset state before queuing

        # A new task instance is required to re-run the logic
        task_class = self._task_registry[task_model.name]
        task_instance = task_class(task_model)

        # Use the original args and kwargs stored in the model
        args = task_model.run_args
        kwargs = task_model.run_kwargs

        if self._can_run_task(task_model.is_exclusive, task_instance.is_blocking):
            logger.info(f"Restarting task '{task_model.name}' ({task_model.id}) immediately.")
            self._start_task_execution(task_instance, *args, **kwargs)
        else:
            logger.info(f"Queueing restarted task '{task_model.name}' ({task_model.id}).")
            await self._pending_tasks.put((task_instance, args, kwargs))

        return task_model

# Singleton instance of the TaskManager
task_manager = TaskManager()

def get_task_manager() -> "TaskManager":
    """FastAPI dependency to get the singleton TaskManager instance."""
    return task_manager
