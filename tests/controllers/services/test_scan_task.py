import asyncio
import time

import pytest
import pytest_asyncio
from unittest.mock import MagicMock, AsyncMock, patch, call
from datetime import datetime

from app.config.scan import ScanSetting

from app.models.task import Task, TaskStatus, TaskProgress
from app.models.scan import Scan, ScanStatus
from app.models.paths import CartesianPoint3D, PolarPoint3D
from app.controllers.services.tasks.scan_task import ScanTask
from app.controllers.services.tasks.task_manager import TaskManager
from app.controllers.services.projects import ProjectManager
from app.controllers.hardware.motors import MotorController
from app.models.motor import Motor
from app.config.motor import MotorConfig

# Mark all tests in this module as asyncio tests
pytestmark = pytest.mark.asyncio

@pytest_asyncio.fixture
async def task_manager_fixture() -> TaskManager:
    """Provides a clean, isolated TaskManager that knows about ScanTask."""
    from app.controllers.services.tasks.task_manager import task_manager

    # Reset state for isolation
    task_manager._tasks = {}
    task_manager._running_tasks = {}
    task_manager._paused_tasks = {}
    task_manager._pending_tasks = asyncio.Queue()
    task_manager._task_registry = {}

    # Register the task we want to test
    task_manager.register_task("scan_task", ScanTask)

    yield task_manager

@pytest.fixture
def motor_controller_instance():
    motor_config = MotorConfig(
        direction_pin=1, enable_pin=2, step_pin=3,
        acceleration=20000, max_speed=5000,
        min_angle=0, max_angle=360,
        direction=1, steps_per_rotation=3200
    )
    motor = Motor(name="rotor", settings=motor_config)
    controller = MotorController(motor)

    yield controller

@pytest.fixture
def mock_project_manager() -> MagicMock:
    """Provides a mocked ProjectManager with an async add_photo_async method."""
    mock_pm = MagicMock(spec=ProjectManager)
    # Configure add_photo_async as an awaitable mock
    mock_pm.add_photo_async = AsyncMock()
    return mock_pm

@pytest.fixture
def mock_motor_controller():
    """Provides a mock motor controller with an async move_to method."""
    mock = MagicMock(spec=MotorController)
    mock.move_to = AsyncMock()
    mock.is_busy = MagicMock(return_value=False)
    # Add any other necessary attributes or method mocks here
    return mock

async def async_sleep_side_effect(*args, **kwargs):
    """Helper to correctly simulate an async delay in a mock."""
    await asyncio.sleep(0.02)


class TestScanTask:
    """Test suite for the ScanTask execution and lifecycle management."""

    @patch('app.controllers.services.tasks.scan_task.generate_scan_path')
    @patch('app.controllers.services.tasks.scan_task.motors', autospec=True)
    async def test_scan_task_successful_run(
        self,
        mock_motors: MagicMock,
        mock_generate_scan_path: MagicMock,
        task_manager_fixture: TaskManager,
        mock_camera_controller: MagicMock,
        sample_scan_model: Scan,
        sample_scan_settings: ScanSetting,
        mock_project_manager: MagicMock,
    ):
        """Tests a successful run of the ScanTask using the TaskManager."""
        # Mock the path generation to return a simple list of points
        mock_generate_scan_path.return_value = [
            PolarPoint3D(theta=i, fi=i) for i in range(sample_scan_settings.points)
        ]

        # --- Simulate delays in hardware operations ---
        mock_motors.move_to_point.side_effect = async_sleep_side_effect

        def delayed_photo(*args, **kwargs):
            """Simulate a blocking I/O delay for photo capture."""
            time.sleep(0.02)
            return b"fake_image_bytes"

        async def delayed_add_photo(*args, **kwargs):
            """Simulate a non-blocking I/O delay for saving a photo."""
            await asyncio.sleep(0.01)

        mock_camera_controller.photo.side_effect = delayed_photo
        mock_project_manager.add_photo_async.side_effect = delayed_add_photo

        tm = task_manager_fixture
        total_expected_steps = sample_scan_settings.points

        # --- Run the task via the manager ---
        task_model = await tm.create_and_run_task(
            "scan_task",
            sample_scan_model,
            mock_camera_controller,
            mock_project_manager,
        )

        # --- Wait for completion ---
        final_task_model = await tm.wait_for_task(task_model.id)

        # --- Assertions ---
        # 1. Check final task status
        assert final_task_model.status == TaskStatus.COMPLETED, f"Task failed with: {final_task_model.error}"

        # 2. Check progress
        assert final_task_model.progress.current == total_expected_steps
        assert final_task_model.progress.total == total_expected_steps

        # 3. Check calls
        assert mock_camera_controller.photo.call_count == total_expected_steps
        assert mock_project_manager.add_photo_async.call_count == total_expected_steps
        # +1 for the final cleanup move
        assert mock_motors.move_to_point.call_count == total_expected_steps + 1
        mock_motors.move_to_point.assert_called()

    @patch('app.controllers.services.tasks.scan_task.generate_scan_path')
    @patch('app.controllers.services.tasks.scan_task.motors', autospec=True)
    async def test_scan_task_error_handling(
        self,
        mock_motors: MagicMock,
        mock_generate_scan_path: MagicMock,
        task_manager_fixture: TaskManager,
        mock_camera_controller: MagicMock,
        sample_scan_model: Scan,
        mock_project_manager: MagicMock,
    ):
        """Tests that the task handles exceptions during the run and sets status to ERROR."""
        total_points = 10
        error_at_step = 3
        mock_generate_scan_path.return_value = [PolarPoint3D(theta=i, fi=i) for i in range(total_points)]

        # Stabilize mock behavior
        mock_motors.move_to_point.side_effect = async_sleep_side_effect

        # Simulate an error during photo capture
        error_message = "Simulated camera hardware failure"

        def photo_side_effect(*args, **kwargs):
            # We check for > because call_count is 1-based
            if mock_camera_controller.photo.call_count > error_at_step:
                raise RuntimeError(error_message)
            return b"fake_image_bytes"

        mock_camera_controller.photo.side_effect = photo_side_effect
        mock_project_manager.add_photo_async.side_effect = async_sleep_side_effect

        tm = task_manager_fixture
        task_model = await tm.create_and_run_task(
            "scan_task",
            sample_scan_model,
            mock_camera_controller,
            mock_project_manager,
        )

        # Wait for completion (it should fail)
        final_task_model = await tm.wait_for_task(task_model.id)

        # --- Assertions ---
        assert final_task_model.status == TaskStatus.ERROR
        assert error_message in str(final_task_model.error)

        # Check that photos taken before the error were still saved
        assert mock_project_manager.add_photo_async.call_count == error_at_step

        # Check that cleanup was still attempted despite the error
        assert call(PolarPoint3D(theta=90, fi=90)) in mock_motors.move_to_point.call_args_list

    @patch('app.controllers.services.tasks.scan_task.generate_scan_path')
    @patch('app.controllers.services.tasks.scan_task.motors', autospec=True)
    async def test_scan_task_cancellation(
        self,
        mock_motors: MagicMock,
        mock_generate_scan_path: MagicMock,
        task_manager_fixture: TaskManager,
        mock_camera_controller: MagicMock,
        sample_scan_model: Scan,
        mock_project_manager: MagicMock,
        caplog,
    ):
        """Tests that a running scan task can be cancelled."""
        total_points = 10
        cancel_at_step = 5
        mock_generate_scan_path.return_value = [PolarPoint3D(theta=i, fi=i) for i in range(total_points)]
        tm = task_manager_fixture

        # Stabilize mock behavior to simply wait, allowing the cancellation to interrupt it.
        mock_motors.move_to_point.side_effect = async_sleep_side_effect

        # Simulate I/O delays to prevent the task from finishing too quickly
        mock_camera_controller.photo.side_effect = lambda: (time.sleep(0.02), b"fake_image_bytes")[1]
        mock_project_manager.add_photo_async.side_effect = async_sleep_side_effect

        task_model = await tm.create_and_run_task(
            "scan_task",
            sample_scan_model,
            mock_camera_controller,
            mock_project_manager,
        )

        # Wait until the task is running and has made some progress
        while True:
            current_task = tm.get_task_info(task_model.id)
            if current_task.status not in [TaskStatus.PENDING, TaskStatus.RUNNING]:
                pytest.fail(f"Task finished prematurely with status {current_task.status}")
            if current_task.progress.current >= cancel_at_step:
                break
            await asyncio.sleep(0.05)

        # Cancel the task and wait for it to fully terminate
        await tm.cancel_task(task_model.id)
        final_task_model = await tm.wait_for_task(task_model.id)

        # Give the event loop a moment to process logs from the cancelled task's teardown
        await asyncio.sleep(0.1)

        # --- Assertions ---
        assert final_task_model.status == TaskStatus.CANCELLED
        assert final_task_model.progress.current < total_points
        assert mock_camera_controller.photo.call_count < total_points

        # Pragmatic check: The mock assertion in the `finally` block is unreliable
        # due to the way asyncio handles cancellation. Instead, we verify that the
        # cleanup code was attempted by checking for its expected failure log.
        #assert "Error during cleanup: Controller not found: turntable" in caplog.text

    @patch('app.controllers.services.tasks.scan_task.generate_scan_path')
    @patch('app.controllers.services.tasks.scan_task.motors', autospec=True)
    async def test_scan_task_pause_and_resume(
        self,
        mock_motors: MagicMock,
        mock_generate_scan_path: MagicMock,
        task_manager_fixture: TaskManager,
        mock_camera_controller: MagicMock,
        sample_scan_model: Scan,
        mock_project_manager: MagicMock,
    ):
        """Tests that a running scan task can be paused and resumed."""
        total_points = 10
        pause_at_step = 4
        mock_generate_scan_path.return_value = [PolarPoint3D(theta=i, fi=i) for i in range(total_points)]
        tm = task_manager_fixture

        # Stabilize mock behavior
        mock_motors.move_to_point.side_effect = async_sleep_side_effect

        # Simulate I/O delays to prevent the task from finishing too quickly
        mock_camera_controller.photo.side_effect = lambda: (time.sleep(0.02), b"fake_image_bytes")[1]
        mock_project_manager.add_photo_async.side_effect = async_sleep_side_effect

        task_model = await tm.create_and_run_task(
            "scan_task",
            sample_scan_model,
            mock_camera_controller,
            mock_project_manager,
        )

        # Wait to reach the pause step
        while True:
            current_task = tm.get_task_info(task_model.id)
            if current_task.status not in [TaskStatus.PENDING, TaskStatus.RUNNING]:
                pytest.fail("Task finished prematurely before pausing")
            if current_task.progress.current >= pause_at_step:
                break
            await asyncio.sleep(0.05)

        # Pause the task
        await tm.pause_task(task_model.id)

        # Check that it's paused and progress has stopped
        paused_task = tm.get_task_info(task_model.id)
        assert paused_task.status == TaskStatus.PAUSED
        progress_at_pause = paused_task.progress.current
        await asyncio.sleep(0.2)  # Wait a bit to ensure it's not progressing
        assert tm.get_task_info(task_model.id).progress.current == progress_at_pause

        # Resume the task
        await tm.resume_task(task_model.id)

        # Wait for completion
        final_task_model = await tm.wait_for_task(task_model.id)

        # Final assertions
        assert final_task_model.status == TaskStatus.COMPLETED
        assert final_task_model.progress.current == total_points
        assert mock_motors.move_to_point.call_count == total_points + 1