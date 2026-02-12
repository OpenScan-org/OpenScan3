import asyncio
import json
import os
import shutil

import pytest
import pytest_asyncio

import openscan_firmware.controllers.services.tasks.task_manager as task_manager_module
from openscan_firmware.controllers.services.tasks.task_manager import TaskManager

TASKS_STORAGE_PATH = task_manager_module.TASKS_STORAGE_PATH
from openscan_firmware.models.task import TaskStatus, Task, TaskProgress


@pytest.fixture
def tasks_storage_dir(task_manager_storage_path):
    """Synchronize module-level TASKS_STORAGE_PATH with the isolated test directory."""

    task_manager_module.TASKS_STORAGE_PATH = task_manager_storage_path
    globals()["TASKS_STORAGE_PATH"] = task_manager_module.TASKS_STORAGE_PATH
    return task_manager_storage_path

# Mark all tests in this module as asyncio tests and ensure storage dir fixture runs
pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("tasks_storage_dir")]


@pytest_asyncio.fixture
async def task_manager_fixture(tasks_storage_dir):
    """
    Provides a clean, isolated TaskManager instance for each test.

    This fixture resets the singleton's state and clears any persisted
    task files from the filesystem before and after each test.
    """
    # --- Filesystem Teardown from previous run ---
    if os.path.exists(task_manager_module.TASKS_STORAGE_PATH):
        shutil.rmtree(task_manager_module.TASKS_STORAGE_PATH)
    os.makedirs(task_manager_module.TASKS_STORAGE_PATH)

    # --- Singleton Reset ---
    # Resetting the singleton instance is crucial for test isolation.
    TaskManager._instance = None
    tm = TaskManager()

    # Discover demo/example tasks via autodiscovery
    tm.autodiscover_tasks(
        namespaces=[
            "openscan_firmware.controllers.services.tasks",
        ],
        extra_ignore_modules={"base_task", "task_manager", "example_tasks"},
        override_on_conflict=False,
    )

    # Register example/demo tasks explicitly (they are ignored by default autodiscovery)
    from openscan_firmware.controllers.services.tasks.examples import demo_examples

    tm.register_task("hello_world_async_task", demo_examples.HelloWorldAsyncTask)
    tm.register_task("hello_world_blocking_task", demo_examples.HelloWorldBlockingTask)
    tm.register_task("exclusive_demo_task", demo_examples.ExclusiveDemoTask)
    tm.register_task("generator_task", demo_examples.ExampleTaskWithGenerator)
    tm.register_task("failing_task", demo_examples.FailingTask)

    yield tm  # Provide the cleaned-up instance to the test

    # --- Teardown after the test has run ---
    # Ensure all tasks are cancelled to not interfere with the next test.
    all_tasks_info = tm.get_all_tasks_info()
    if all_tasks_info:
        cancellation_tasks = [
            tm.cancel_task(task.id)
            for task in all_tasks_info
            if task.status in [TaskStatus.RUNNING, TaskStatus.PENDING, TaskStatus.PAUSED]
        ]
        if cancellation_tasks:
            await asyncio.gather(*cancellation_tasks, return_exceptions=True)
            await asyncio.sleep(0.01)

    # --- Final Filesystem Cleanup ---
    if os.path.exists(task_manager_module.TASKS_STORAGE_PATH):
        shutil.rmtree(task_manager_module.TASKS_STORAGE_PATH)

    # --- Final Singleton Reset ---
    TaskManager._instance = None


async def wait_for_task_completion(tm: TaskManager, task_id: str, timeout: float = 10.0):
    """Helper coroutine to wait for a task to reach a terminal state."""
    start_time = asyncio.get_event_loop().time()
    while True:
        task = tm.get_task_info(task_id)
        if task.status in [TaskStatus.COMPLETED, TaskStatus.ERROR, TaskStatus.CANCELLED]:
            return task
        if (asyncio.get_event_loop().time() - start_time) > timeout:
            pytest.fail(f"Task {task_id} did not complete within {timeout}s. Current status: {task.status}")
        await asyncio.sleep(0.05)


async def wait_for_status_in_file(file_path: str, expected_status: str, timeout: float = 2.0):
    """Helper coroutine to poll a task's JSON file until a specific status is found."""
    start_time = asyncio.get_event_loop().time()
    last_status = None
    while (asyncio.get_event_loop().time() - start_time) < timeout:
        if os.path.exists(file_path):
            with open(file_path, 'r') as f:
                try:
                    data = json.load(f)
                    last_status = data.get('status')
                    if last_status == expected_status:
                        return
                except (json.JSONDecodeError, IOError):
                    # Ignore errors from reading a partially written file
                    pass
        await asyncio.sleep(0.05)
    pytest.fail(
        f"Expected status '{expected_status}' not found in {file_path} within {timeout}s. "
        f"Last found status: '{last_status}'."
    )


async def test_create_and_run_task(task_manager_fixture: TaskManager):
    """
    Tests that a simple async task can be created and run successfully.
    """
    tm = task_manager_fixture
    task = await tm.create_and_run_task("hello_world_async_task", total_steps=2)

    assert task is not None
    assert task.name == "hello_world_async_task"

    final_task_state = await tm.wait_for_task(task.id)
    assert final_task_state.status == TaskStatus.COMPLETED
    assert "Completed 2 steps" in final_task_state.result


async def test_non_exclusive_task_concurrency_limit(task_manager_fixture: TaskManager):
    """
    Tests that the TaskManager correctly limits the number of concurrent non-exclusive tasks.
    """
    tm = task_manager_fixture
    # The limit is currently 3, so we start 4 tasks.
    task_count = 4
    
    tasks = []
    for _ in range(task_count):
        # Use a long-running task to ensure they don't finish too quickly
        task = await tm.create_and_run_task("hello_world_async_task", total_steps=10)
        tasks.append(task)

    await asyncio.sleep(0.1) # Allow time for tasks to be processed and statuses updated

    running_tasks = [t for t in tasks if tm.get_task_info(t.id).status == TaskStatus.RUNNING]
    pending_tasks = [t for t in tasks if tm.get_task_info(t.id).status == TaskStatus.PENDING]

    assert len(running_tasks) == 3
    assert len(pending_tasks) == 1

    # Now, wait for one of the running tasks to complete
    first_running_task_id = running_tasks[0].id
    completed_task = await wait_for_task_completion(tm, first_running_task_id, timeout=15)
    assert completed_task.status == TaskStatus.COMPLETED

    # The pending task should now start running
    await asyncio.sleep(0.1) # Allow time for the queue to be processed
    pending_task_model = tm.get_task_info(pending_tasks[0].id)
    assert pending_task_model.status == TaskStatus.RUNNING


async def test_exclusive_task_blocks_others(task_manager_fixture: TaskManager):
    """
    Tests that a running exclusive task prevents other tasks from starting.
    """
    tm = task_manager_fixture

    # Start an exclusive task
    exclusive_task = await tm.create_and_run_task("exclusive_demo_task", duration=2)
    await asyncio.sleep(0.1)  # Give it time to start and occupy the runner

    # Try to start a non-exclusive task while the exclusive one is running
    non_exclusive_task = await tm.create_and_run_task("hello_world_async_task", total_steps=1)
    await asyncio.sleep(0.1)  # Give the manager time to process the new task

    # The exclusive task should be running, the new one should be pending
    assert tm.get_task_info(exclusive_task.id).status == TaskStatus.RUNNING
    assert tm.get_task_info(non_exclusive_task.id).status == TaskStatus.PENDING

    # Wait for the exclusive task to finish
    await wait_for_task_completion(tm, exclusive_task.id, timeout=3)

    # Now, the pending task should have started
    #await asyncio.sleep(0.1)  # Give the queue processor time to run
    status = tm.get_task_info(non_exclusive_task.id).status
    # since the pending task may finish quickly, we are also allowing COMPLETED state
    assert status in (TaskStatus.RUNNING, TaskStatus.COMPLETED)



async def test_exclusive_task_waits_for_others(task_manager_fixture: TaskManager):
    """
    Tests that an exclusive task waits for currently running non-exclusive tasks to complete.
    """
    tm = task_manager_fixture

    # Start a non-exclusive task
    non_exclusive_task = await tm.create_and_run_task("hello_world_async_task", total_steps=2)
    await asyncio.sleep(0.1)  # Give it time to start

    # Try to start an exclusive task
    exclusive_task = await tm.create_and_run_task("exclusive_demo_task", duration=1)
    await asyncio.sleep(0.1)  # Give the manager time to process it

    # The non-exclusive task should be running, the exclusive one should be pending
    assert tm.get_task_info(non_exclusive_task.id).status == TaskStatus.RUNNING
    assert tm.get_task_info(exclusive_task.id).status == TaskStatus.PENDING

    # Wait for the non-exclusive task to finish
    await wait_for_task_completion(tm, non_exclusive_task.id, timeout=10)

    # Now, the pending exclusive task should have started
    await asyncio.sleep(0.1)  # Give the queue processor time to run
    assert tm.get_task_info(exclusive_task.id).status == TaskStatus.RUNNING


async def test_exclusive_task_starvation_prevention(task_manager_fixture: TaskManager):
    """
    Tests that a pending exclusive task prevents new non-exclusive tasks from starting,
    ensuring the exclusive task does not 'starve'.
    """
    tm = task_manager_fixture

    # Helper to print the current state for debugging
    def print_state(tag: str):
        print(f"\n--- STATE: {tag} ---")
        running_async = list(tm._running_async_tasks.keys())
        running_blocking = list(tm._running_blocking_tasks.keys())
        # Create a list of task IDs from the queue to see the order
        pending_ids = [t_instance.id for t_instance, _, _ in list(tm._pending_tasks._queue)]
        print(f"Running Async: {[task_id[-6:] for task_id in running_async]}")
        print(f"Running Blocking: {[task_id[-6:] for task_id in running_blocking]}")
        print(f"Pending Queue: {[task_id[-6:] for task_id in pending_ids]}")

        # Safely get all task infos
        all_tasks = {}
        if 'running_tasks' in locals() and running_tasks:
            all_tasks.update({f"initial_async_{i}": task.id for i, task in enumerate(running_tasks)})
        if 'exclusive_task' in locals() and exclusive_task:
            all_tasks["exclusive"] = exclusive_task.id
        if 'another_non_exclusive_task' in locals() and another_non_exclusive_task:
            all_tasks["another_async"] = another_non_exclusive_task.id

        for name, task_id in all_tasks.items():
            try:
                info = tm.get_task_info(task_id)
                print(f"  - {name} ({task_id[-6:]}): {info.status.value}")
            except KeyError:
                print(f"  - {name} ({task_id[-6:]}): NOT FOUND")
        print("---------------------\n")

    # The limit is 3, so we start 3 tasks to fill the slots.
    # Use a task that takes a bit of time.
    running_tasks = [
        await tm.create_and_run_task("hello_world_async_task", total_steps=3) for _ in range(3)
    ]
    await asyncio.sleep(0.1) # Let them start

    # Now, queue an exclusive task. It should become PENDING.
    exclusive_task = await tm.create_and_run_task("exclusive_demo_task", duration=0.5)
    await asyncio.sleep(0.1)
    assert tm.get_task_info(exclusive_task.id).status == TaskStatus.PENDING

    # Queue another non-exclusive task. Because an exclusive task is pending,
    # this one should also be PENDING, not RUNNING.
    another_non_exclusive_task = await tm.create_and_run_task("hello_world_async_task", total_steps=1)
    await asyncio.sleep(0.1)
    assert tm.get_task_info(another_non_exclusive_task.id).status == TaskStatus.PENDING

    print_state("Before waiting for any task to complete")

    # Wait for one of the initial running tasks to complete
    await wait_for_task_completion(tm, running_tasks[0].id, timeout=10)

    print_state("Immediately after one task completed")

    await asyncio.sleep(0.1) # Give queue processor time

    print_state("After waiting for queue processor to run")

    # At this point, even with a free slot, the pending non-exclusive task should NOT have started.
    # The system should be waiting for all tasks to finish to start the exclusive one.
    assert tm.get_task_info(another_non_exclusive_task.id).status == TaskStatus.PENDING


async def test_pause_and_resume_task(task_manager_fixture: TaskManager):
    """
    Tests pausing and resuming a running task.
    """
    tm = task_manager_fixture
    total_steps = 4
    step_interval = 0.2

    # Create a task that runs for a predictable amount of time
    task = await tm.create_and_run_task("generator_task", total_steps=total_steps, interval=step_interval)

    # Let the task run for a bit
    await asyncio.sleep(step_interval * 1.5)

    # Pause the task and check status
    paused_task = await tm.pause_task(task.id)
    assert paused_task.status == TaskStatus.PAUSED

    # Wait while paused
    await asyncio.sleep(0.3)

    # Check that status is still paused
    info_after_pause = tm.get_task_info(task.id)
    assert info_after_pause.status == TaskStatus.PAUSED

    # Resume the task
    resumed_task = await tm.resume_task(task.id)
    assert resumed_task.status == TaskStatus.RUNNING

    # Let it finish
    final_task_state = await tm.wait_for_task(task.id, timeout=(total_steps * step_interval + 2))
    assert final_task_state.status == TaskStatus.COMPLETED


async def test_streaming_task_progress(task_manager_fixture: TaskManager):
    """
    Tests that a task using an async generator correctly streams progress updates.
    """
    tm = task_manager_fixture
    total_steps = 5
    task = await tm.create_and_run_task("generator_task", total_steps=total_steps, interval=0.1)

    # Allow the task a moment to initialize and set its total.
    await asyncio.sleep(0.01)

    # Check initial progress
    task_info = tm.get_task_info(task.id)
    assert task_info.progress.total == total_steps

    # Wait for the task to complete
    final_task_state = await tm.wait_for_task(task.id)
    assert final_task_state.status == TaskStatus.COMPLETED
    assert final_task_state.progress.current == total_steps


async def test_streaming_task_cancel_and_restart(task_manager_fixture: TaskManager):
    """
    Tests cancelling and restarting a streaming task.
    """
    tm = task_manager_fixture
    total_steps = 10
    task = await tm.create_and_run_task("generator_task", total_steps=total_steps, interval=0.1)

    # Let it run halfway
    await asyncio.sleep(total_steps * 0.1 / 2)

    # Cancel the task
    cancelled_task = await tm.cancel_task(task.id)
    await asyncio.sleep(0.1) # Allow cancellation to propagate

    cancelled_task_info = tm.get_task_info(task.id)
    assert cancelled_task_info.status == TaskStatus.CANCELLED
    assert cancelled_task_info.progress.current > 0
    assert cancelled_task_info.progress.current < total_steps
    last_progress_before_restart = cancelled_task_info.progress.current

    # Restart the task
    restarted_task = await tm.restart_task(task.id)
    assert restarted_task.status == TaskStatus.RUNNING
    # Progress should be reset upon restart
    assert restarted_task.progress.current == 0  # Should start from the beginning

    final_task_state = await tm.wait_for_task(restarted_task.id, timeout=10)
    assert final_task_state.status == TaskStatus.COMPLETED
    assert final_task_state.progress.current == total_steps


async def test_blocking_task_does_not_block_event_loop(task_manager_fixture: TaskManager):
    """
    Tests that a blocking task runs in a separate thread and does not block
    the main asyncio event loop.
    """
    tm = task_manager_fixture
    blocking_duration = 0.5

    start_time = asyncio.get_event_loop().time()

    # Start a blocking task
    blocking_task = await tm.create_and_run_task("hello_world_blocking_task", duration=blocking_duration)

    # Give the scheduler a moment to process the first task
    await asyncio.sleep(0.01)

    # Immediately start another quick, non-blocking task
    non_blocking_task = await tm.create_and_run_task("hello_world_async_task", total_steps=1, interval=0.01)

    # The non-blocking task should complete very quickly, long before the blocking one
    await tm.wait_for_task(non_blocking_task.id, timeout=0.2)

    end_time = asyncio.get_event_loop().time()

    # Check that we didn't have to wait for the blocking task to finish
    assert (end_time - start_time) < blocking_duration

    # And the blocking task should eventually complete successfully
    final_blocking_state = await tm.wait_for_task(blocking_task.id, timeout=blocking_duration + 0.2)
    assert final_blocking_state.status == TaskStatus.COMPLETED
    assert final_blocking_state.result == "Blocking task complete."


async def test_single_blocking_task_completes(task_manager_fixture: TaskManager):
    """
    Tests that a single, simple blocking task runs to completion successfully.
    """
    tm = task_manager_fixture
    blocking_duration = 0.2

    # Start a blocking task
    task = await tm.create_and_run_task("hello_world_blocking_task", duration=blocking_duration)
    assert task.status == TaskStatus.RUNNING

    # Wait for it to complete
    final_state = await tm.wait_for_task(task.id, timeout=blocking_duration + 0.5)

    # Check that it completed successfully
    assert final_state.status == TaskStatus.COMPLETED
    assert final_state.result == "Blocking task complete."


async def test_cancel_running_task(task_manager_fixture: TaskManager):
    """
    Tests that a running task can be cancelled.
    """
    tm = task_manager_fixture
    task = await tm.create_and_run_task("hello_world_async_task", total_steps=10)  # Long running

    await asyncio.sleep(0.5)  # Let it start
    assert tm.get_task_info(task.id).status == TaskStatus.RUNNING

    # Cancel the task
    await tm.cancel_task(task.id)

    cancelled_task = await wait_for_task_completion(tm, task.id, timeout=2)
    assert cancelled_task.status == TaskStatus.CANCELLED


async def test_cancel_pending_task(task_manager_fixture: TaskManager):
    """
    Tests that a pending task can be cancelled before it even starts.
    """
    tm = task_manager_fixture
    # Block the runner with an exclusive task
    exclusive_task = await tm.create_and_run_task("exclusive_demo_task", duration=3)

    # Create a new task that will be pending
    pending_task = await tm.create_and_run_task("hello_world_async_task", total_steps=1)
    await asyncio.sleep(0.1)  # Let the queue process
    assert tm.get_task_info(pending_task.id).status == TaskStatus.PENDING

    # Cancel the pending task
    await tm.cancel_task(pending_task.id)
    assert tm.get_task_info(pending_task.id).status == TaskStatus.CANCELLED

    # Ensure the exclusive task can still complete
    await wait_for_task_completion(tm, exclusive_task.id, timeout=4)


# --- Tests for Persistence ---

async def test_task_state_is_persisted_across_lifecycle(task_manager_fixture: TaskManager):
    """
    Tests that a task's state is correctly saved to a JSON file at each lifecycle stage.
    """
    tm = task_manager_fixture
    task = await tm.create_and_run_task("generator_task", total_steps=4, interval=0.2)
    task_file_path = TASKS_STORAGE_PATH / f"{task.id}.json"

    # 1. Wait for the status to become 'running' in the persisted file.
    await wait_for_status_in_file(task_file_path, 'running')

    # Now we can safely check the content
    with open(task_file_path, 'r') as f:
        data = json.load(f)
    assert data['status'] == 'running'

    # 2. Pause the task and check persisted state
    await asyncio.sleep(0.3)  # Let it run a bit
    await tm.pause_task(task.id)
    with open(task_file_path, 'r') as f:
        data = json.load(f)
    assert data['status'] == 'paused'

    # 3. Resume the task and check persisted state
    await tm.resume_task(task.id)
    with open(task_file_path, 'r') as f:
        data = json.load(f)
    assert data['status'] == 'running'

    # 4. Wait for completion and check final persisted state
    final_state = await wait_for_task_completion(tm, task.id, timeout=5)
    assert final_state.status == TaskStatus.COMPLETED
    with open(task_file_path, 'r') as f:
        data = json.load(f)
    assert data['status'] == 'completed'


async def test_tasks_are_reloaded_on_startup(task_manager_fixture: TaskManager):
    """
    Tests that tasks are correctly reloaded from the filesystem when a new
    TaskManager instance is created, simulating an application restart.
    """
    tm = task_manager_fixture

    # --- Simulate a previous application run ---
    # Manually create task models for REAL task types and save them to disk.
    completed_task = Task(name="completed_task", task_type="hello_world_async_task", status=TaskStatus.COMPLETED)
    running_task = Task(name="running_task", task_type="hello_world_async_task", status=TaskStatus.RUNNING)
    paused_task = Task(name="paused_task", task_type="generator_task", status=TaskStatus.PAUSED)

    with open(TASKS_STORAGE_PATH / f"{completed_task.id}.json", 'w') as f:
        f.write(completed_task.model_dump_json())
    with open(TASKS_STORAGE_PATH / f"{running_task.id}.json", 'w') as f:
        f.write(running_task.model_dump_json())
    with open(TASKS_STORAGE_PATH / f"{paused_task.id}.json", 'w') as f:
        f.write(paused_task.model_dump_json())

    # --- Simulate Application Restart ---
    # Instead of creating a new instance, we clear the internal state of the
    # existing (and correctly configured) manager and trigger the loading manually.
    tm._tasks = {}
    tm.restore_tasks_from_persistence()

    # --- Verification ---
    # Check if all non-completed tasks were loaded
    all_loaded_tasks = tm.get_all_tasks_info()
    assert len(all_loaded_tasks) == 2  # Completed task should be cleaned up

    loaded_task_ids = {t.id for t in all_loaded_tasks}
    assert running_task.id in loaded_task_ids
    assert paused_task.id in loaded_task_ids
    assert completed_task.id not in loaded_task_ids

    # Verify the completed task's file was deleted
    assert not os.path.exists(TASKS_STORAGE_PATH / f"{completed_task.id}.json")

    # Verify that the other tasks were correctly marked as INTERRUPTED
    for task in all_loaded_tasks:
        assert task.status == TaskStatus.INTERRUPTED


async def test_cancelled_task_state_is_persisted(task_manager_fixture: TaskManager):
    """Tests that a cancelled task's final state is saved to its JSON file."""
    tm = task_manager_fixture
    task = await tm.create_and_run_task("generator_task", total_steps=10, interval=0.1)
    task_file_path = TASKS_STORAGE_PATH / f"{task.id}.json"

    await asyncio.sleep(0.3)  # Let it run a bit

    # Cancel the task
    await tm.cancel_task(task.id)

    # Wait for the task to reach the cancelled state
    await wait_for_task_completion(tm, task.id, timeout=2)

    # Check the persisted file
    with open(task_file_path, 'r') as f:
        data = json.load(f)

    assert data['status'] == TaskStatus.CANCELLED.value
    assert "cancelled" in data['error'].lower()


async def test_failed_task_state_is_persisted(task_manager_fixture: TaskManager):
    """Tests that a failed task's error state is saved to its JSON file."""
    tm = task_manager_fixture
    error_msg = "This is a specific failure message."
    task = await tm.create_and_run_task("failing_task", error_message=error_msg)
    task_file_path = TASKS_STORAGE_PATH / f"{task.id}.json"

    # Wait for the task to fail
    final_state = await wait_for_task_completion(tm, task.id, timeout=2)
    assert final_state.status == TaskStatus.ERROR

    # Check the persisted file for the correct status and error message
    with open(task_file_path, 'r') as f:
        data = json.load(f)

    assert data['status'] == TaskStatus.ERROR.value
    assert data['error'] == error_msg


async def test_startup_with_corrupt_task_files(task_manager_fixture: TaskManager, caplog):
    """Tests that the TaskManager can handle corrupt/invalid task files on startup."""
    tm = task_manager_fixture

    # 1. Create a valid task file that is in a terminal but not 'COMPLETED' state.
    #    This ensures it won't be cleaned up on restart.
    valid_task_to_preserve = await tm.create_and_run_task("hello_world_async_task")
    await tm.cancel_task(valid_task_to_preserve.id)
    await wait_for_task_completion(tm, valid_task_to_preserve.id)  # Wait for cancellation to finish
    assert tm.get_task_info(valid_task_to_preserve.id).status == TaskStatus.CANCELLED

    # 2. Create a corrupt JSON file in the tasks directory.
    corrupt_file_path = TASKS_STORAGE_PATH / "corrupt.json"
    with open(corrupt_file_path, 'w') as f:
        f.write('{"id": "abc", "name": "corrupt"}')  # Malformed JSON

    # 3. Create a file with invalid data (doesn't match Task model).
    invalid_data_file_path = TASKS_STORAGE_PATH / "invalid_data.json"
    with open(invalid_data_file_path, 'w') as f:
        json.dump({"id": "def", "status": "unknown"}, f)  # Missing required fields

    # 4. Simulate an application restart by clearing state and reloading.
    tm._tasks = {}
    tm.restore_tasks_from_persistence()

    # 5. Check that only the valid, non-completed task was loaded.
    loaded_tasks = tm.get_all_tasks_info()
    assert len(loaded_tasks) == 1
    assert loaded_tasks[0].id == valid_task_to_preserve.id
    assert loaded_tasks[0].status == TaskStatus.CANCELLED

    # 6. Check that warnings were logged for the bad files
    assert "Could not load or process task file 'corrupt.json'" in caplog.text
    assert "Could not load or process task file 'invalid_data.json'" in caplog.text


async def test_blocking_tasks_ignore_concurrency_limit(task_manager_fixture: TaskManager):
    """
    Regression test to ensure blocking tasks ignore the async concurrency limit.
    This test is made deterministic using an asyncio.Event.
    """
    tm = task_manager_fixture
    # Temporarily lower the limit for this specific test case
    tm.max_concurrent_non_exclusive_tasks = 2

    # Use an event to control when the async tasks finish
    async_task_can_finish_event = asyncio.Event()

    # 1. Start two async tasks that will wait for our event. This fills up the concurrency slots.
    async_task_1 = await tm.create_and_run_task("hello_world_async_task", wait_for_event=async_task_can_finish_event)
    async_task_2 = await tm.create_and_run_task("hello_world_async_task", wait_for_event=async_task_can_finish_event)

    await asyncio.sleep(0.05)  # Give scheduler time to start them
    assert tm.get_task_info(async_task_1.id).status == TaskStatus.RUNNING
    assert tm.get_task_info(async_task_2.id).status == TaskStatus.RUNNING

    # 2. Start a third async task, which should be queued because the slots are full.
    async_task_3_queued = await tm.create_and_run_task("hello_world_async_task", delay=0.1)
    await asyncio.sleep(0.05)  # Give scheduler time to process
    assert tm.get_task_info(async_task_3_queued.id).status == TaskStatus.PENDING

    # 3. Start a blocking task. It should run immediately, ignoring the async limit.
    blocking_task = await tm.create_and_run_task("hello_world_blocking_task", duration=0.1)
    await asyncio.sleep(0.05)  # Give scheduler time to start it in the executor
    assert tm.get_task_info(blocking_task.id).status == TaskStatus.RUNNING

    # 4. Final check of all internal states
    assert len(tm._running_async_tasks) == 2
    assert tm._pending_tasks.qsize() == 1
    assert len(tm._running_blocking_tasks) == 1

    # 5. Cleanup: Allow all tasks to finish
    async_task_can_finish_event.set()  # Release the waiting async tasks
    await wait_for_task_completion(tm, async_task_1.id, timeout=2)
    await wait_for_task_completion(tm, async_task_2.id, timeout=2)
    await wait_for_task_completion(tm, async_task_3_queued.id, timeout=2)
    await wait_for_task_completion(tm, blocking_task.id, timeout=2)


async def test_restart_interrupted_task_after_shutdown(task_manager_fixture: TaskManager):
    """
    Tests that a task interrupted by a shutdown can be correctly reloaded and restarted.
    This simulates a full application restart.
    """
    tm = task_manager_fixture
    total_steps = 10

    # 1. Start a task that will be 'interrupted'
    task = await tm.create_and_run_task("generator_task", total_steps=total_steps, interval=0.1)
    await asyncio.sleep(total_steps * 0.1 / 2)  # Let it run halfway

    task_info = tm.get_task_info(task.id)
    assert task_info.status == TaskStatus.RUNNING
    progress_before_shutdown = task_info.progress.current
    assert 0 < progress_before_shutdown < total_steps

    # --- 2. Simulate Application Shutdown & Restart ---
    # The task state is already persisted on disk. We just clear the manager's
    # internal state and reload from disk.
    task_id_to_restart = task.id
    tm._tasks = {}
    tm.restore_tasks_from_persistence()

    # 3. Verify the task was loaded correctly
    reloaded_task = tm.get_task_info(task_id_to_restart)
    assert reloaded_task is not None
    assert reloaded_task.status == TaskStatus.INTERRUPTED
    assert reloaded_task.error == "Task was interrupted by application shutdown."
    assert reloaded_task.progress.current == progress_before_shutdown

    # 4. Restart the task and verify it runs to completion
    restarted_task = await tm.restart_task(task_id_to_restart)
    assert restarted_task.status == TaskStatus.RUNNING

    final_state = await tm.wait_for_task(restarted_task.id, timeout=2)
    assert final_state.status == TaskStatus.COMPLETED
    assert final_state.progress.current == total_steps


async def test_cancel_pending_task_in_full_queue(task_manager_fixture: TaskManager):
    """
    Tests that a task waiting due to concurrency limits can be cancelled.

    This ensures that cancelling a PENDING task works not just when blocked
    by an exclusive task, but also when waiting in a full queue.
    """
    tm = task_manager_fixture
    concurrency_limit = 3  # Based on the current TaskManager implementation

    # 1. Fill the concurrent task slots
    running_tasks = []
    for _ in range(concurrency_limit):
        task = await tm.create_and_run_task("hello_world_async_task", total_steps=5)
        running_tasks.append(task)

    await asyncio.sleep(0.1)  # Allow tasks to start running

    # 2. Create one more task, which should be PENDING
    pending_task = await tm.create_and_run_task("hello_world_async_task", total_steps=1)
    await asyncio.sleep(0.1)  # Allow scheduler to process

    assert tm.get_task_info(pending_task.id).status == TaskStatus.PENDING
    # The internal queue should have 1 item
    assert tm._pending_tasks.qsize() == 1

    # 3. Cancel the pending task
    await tm.cancel_task(pending_task.id)
    await asyncio.sleep(0.1)  # Allow cancellation to process

    # 4. Verify its status is CANCELLED and it's removed from the queue
    cancelled_task_info = tm.get_task_info(pending_task.id)
    assert cancelled_task_info.status == TaskStatus.CANCELLED
    assert tm._pending_tasks.qsize() == 0

    # 5. Wait for one of the initial running tasks to complete
    await wait_for_task_completion(tm, running_tasks[0].id, timeout=10)
    await asyncio.sleep(0.1)  # Give the scheduler time to run and potentially start a new task

    # 6. Verify the cancelled task is still cancelled and did not run
    final_info = tm.get_task_info(pending_task.id)
    assert final_info.status == TaskStatus.CANCELLED
    assert final_info.progress.current == 0
    assert final_info.result is None


async def test_exclusive_tasks_respect_fifo_order(task_manager_fixture: TaskManager):
    """
    Tests that multiple pending exclusive tasks are executed in FIFO order.
    """
    tm = task_manager_fixture
    completion_log = []

    # 1. Start a controllable task to block the queue
    blocker_event = asyncio.Event()
    blocker_task = await tm.create_and_run_task(
        "hello_world_async_task",
        wait_for_event=blocker_event,
    )

    for _ in range(50):
        if tm.get_task_info(blocker_task.id).status == TaskStatus.RUNNING:
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("Blocker task did not reach RUNNING state in time")

    # 2. Queue two exclusive tasks. They should both become PENDING.
    exclusive_task_A = await tm.create_and_run_task("exclusive_demo_task", duration=0.21)
    await asyncio.sleep(0.01)  # Ensure order of arrival in queue
    exclusive_task_B = await tm.create_and_run_task("exclusive_demo_task", duration=0.22)
    await asyncio.sleep(0.1)

    assert tm.get_task_info(exclusive_task_A.id).status == TaskStatus.PENDING
    assert tm.get_task_info(exclusive_task_B.id).status == TaskStatus.PENDING

    # 3. Wait for the initial blocker task to complete
    blocker_event.set()
    await wait_for_task_completion(tm, blocker_task.id, timeout=10)

    # 4. Immediately after, Task A should start. Task B should still be pending.
    await asyncio.sleep(0.1)  # Give scheduler time
    assert tm.get_task_info(exclusive_task_A.id).status == TaskStatus.RUNNING
    assert tm.get_task_info(exclusive_task_B.id).status == TaskStatus.PENDING

    # 5. Wait for Task A to complete
    task_a_final = await wait_for_task_completion(tm, exclusive_task_A.id, timeout=2)
    assert isinstance(task_a_final.result, dict)
    assert task_a_final.result.get('duration') == 0.21
    completion_log.append("A")

    # 6. Immediately after, Task B should start
    await asyncio.sleep(0.1)  # Give scheduler time
    assert tm.get_task_info(exclusive_task_B.id).status == TaskStatus.RUNNING

    # 7. Wait for Task B to complete
    task_b_final = await wait_for_task_completion(tm, exclusive_task_B.id, timeout=2)
    assert isinstance(task_b_final.result, dict)
    assert task_b_final.result.get('duration') == 0.22
    completion_log.append("B")

    # 8. Check the completion order
    assert completion_log == ["A", "B"]

    # --- Tests for Deletion and Cleanup ---

async def test_delete_task_functionality(task_manager_fixture: TaskManager):
    """
    Tests the `delete_task` method for tasks in various states.
    """
    tm = task_manager_fixture

    # 1. Create a task and cancel it to get it into a terminal state
    cancelled_task = await tm.create_and_run_task("hello_world_async_task", total_steps=10)
    await asyncio.sleep(0.1)
    await tm.cancel_task(cancelled_task.id)
    await asyncio.sleep(0.1)
    cancelled_task_info = tm.get_task_info(cancelled_task.id)
    assert cancelled_task_info.status == TaskStatus.CANCELLED
    task_file_path = TASKS_STORAGE_PATH / f"{cancelled_task.id}.json"
    assert os.path.exists(task_file_path)

    # 2. Delete the cancelled task
    await tm.delete_task(cancelled_task.id)

    # 3. Verify it's gone from memory and disk
    assert tm.get_task_info(cancelled_task.id) is None
    assert not os.path.exists(task_file_path)

    # 4. Create a running task and verify it cannot be deleted
    running_task = await tm.create_and_run_task("hello_world_async_task", total_steps=10)
    await asyncio.sleep(0.1)
    assert tm.get_task_info(running_task.id).status == TaskStatus.RUNNING
    with pytest.raises(ValueError, match="Cannot delete task"):
        await tm.delete_task(running_task.id)

    # 5. Ensure deleting a non-existent task doesn't raise an error
    await tm.delete_task("non-existent-id")


async def test_auto_cleanup_of_completed_tasks_on_startup(task_manager_fixture: TaskManager):
    """
    Tests that completed tasks are automatically cleaned up on TaskManager restart,
    while other terminal-state tasks (e.g., CANCELLED) are preserved.
    """
    tm = task_manager_fixture

    # 1. Create one task that will complete and one that will be cancelled
    completed_task = await tm.create_and_run_task("hello_world_async_task", total_steps=1)
    cancelled_task = await tm.create_and_run_task("hello_world_async_task", total_steps=5)

    await tm.wait_for_task(completed_task.id)
    await tm.cancel_task(cancelled_task.id)
    await asyncio.sleep(0.1)  # ensure cancellation is processed

    completed_task_path = TASKS_STORAGE_PATH / f"{completed_task.id}.json"
    cancelled_task_path = TASKS_STORAGE_PATH / f"{cancelled_task.id}.json"

    assert tm.get_task_info(completed_task.id).status == TaskStatus.COMPLETED
    assert os.path.exists(completed_task_path)
    assert tm.get_task_info(cancelled_task.id).status == TaskStatus.CANCELLED
    assert os.path.exists(cancelled_task_path)

    # 2. Simulate an application restart by clearing state and reloading
    tm._tasks = {}
    tm.restore_tasks_from_persistence()

    # 3. Verify the state after restart
    # The completed task should be gone (cleaned up)
    assert tm.get_task_info(completed_task.id) is None
    assert not os.path.exists(completed_task_path)

    # The cancelled task should still exist
    assert tm.get_task_info(cancelled_task.id) is not None
    assert tm.get_task_info(cancelled_task.id).status == TaskStatus.CANCELLED
    assert os.path.exists(cancelled_task_path)


async def test_startup_with_unregistered_task_type(task_manager_fixture: TaskManager):
    """
    Tests that a task with an unregistered type is handled gracefully on startup.

    It should be loaded, but its status should be set to ERROR with a
    descriptive message, preventing runtime failures later.
    """
    # The fixture provides a clean environment. We will simulate a restart within the test.
    # 1. Manually create a task file for a task type that we will not register.
    unregistered_task_type = "unregistered_legacy_task"
    task_id = "unregistered-task-123"

    # Create a valid Task object that was 'running' before shutdown
    rogue_task = Task(
        id=task_id,
        task_type=unregistered_task_type,
        name="Legacy Task",
        status=TaskStatus.RUNNING
    )
    task_file_path = TASKS_STORAGE_PATH / f"{rogue_task.id}.json"
    with open(task_file_path, 'w') as f:
        f.write(rogue_task.model_dump_json())

    # 2. Simulate a restart by creating a new TaskManager instance.
    # This will trigger _load_tasks_on_startup.
    TaskManager._instance = None
    restarted_tm = TaskManager()
    restarted_tm.restore_tasks_from_persistence()

    # No need to register standard tasks for this test; we only verify that
    # an unregistered task type is loaded into memory with ERROR status.

    # 3. Verify the state of the loaded task.
    loaded_task_info = restarted_tm.get_task_info(task_id)

    assert loaded_task_info is not None, "Task should have been loaded from the file."
    assert loaded_task_info.status == TaskStatus.ERROR, "Task status should be set to ERROR."
    assert loaded_task_info.error == f"Task type '{unregistered_task_type}' is not registered. Cannot restore."
    assert loaded_task_info.task_type == unregistered_task_type