import asyncio
import time
import json
import logging
import io

import pytest
import pytest_asyncio
from unittest.mock import MagicMock, AsyncMock, patch, call
from datetime import datetime

from openscan_firmware.config.scan import ScanSetting

from openscan_firmware.models.task import Task, TaskStatus, TaskProgress
from openscan_firmware.models.scan import Scan
from openscan_firmware.models.paths import CartesianPoint3D, PolarPoint3D
from openscan_firmware.controllers.services.tasks.task_manager import TaskManager
from openscan_firmware.controllers.services.projects import ProjectManager
from openscan_firmware.controllers.hardware.motors import MotorController
from openscan_firmware.models.motor import Motor
from openscan_firmware.config.motor import MotorConfig
from openscan_firmware.models.camera import PhotoData
from openscan_firmware.models.camera import CameraMetadata


@pytest_asyncio.fixture
async def task_manager_fixture() -> TaskManager:
    """Provides a clean, isolated TaskManager with autodiscovered core ScanTask."""
    # Reset singleton and create isolated manager
    TaskManager._instance = None
    tm = TaskManager()
    tm._tasks = {}
    tm._running_tasks = {}
    tm._paused_tasks = {}
    tm._pending_tasks = asyncio.Queue()
    tm._task_registry = {}

    # Autodiscover tasks from packages
    tm.autodiscover_tasks(
        namespaces=["openscan_firmware.controllers.services.tasks"],
        include_subpackages=True,
        ignore_modules={"base_task", "task_manager", "example_tasks"},
        safe_mode=True,
        override_on_conflict=False,
        require_explicit_name=True,
        raise_on_missing_name=True,
    )

    yield tm

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

    @pytest.mark.asyncio
    @patch('openscan_firmware.controllers.hardware.cameras.camera.get_camera_controller')
    @patch('openscan_firmware.controllers.services.tasks.core.scan_task.get_project_manager')
    @patch('openscan_firmware.controllers.services.tasks.core.scan_task.generate_scan_path')
    @patch('openscan_firmware.controllers.hardware.motors', create=True)
    async def test_scan_task_successful_run(
        self,
        mock_motors: MagicMock,
        mock_generate_scan_path: MagicMock,
        mock_get_project_manager: MagicMock,
        mock_get_camera_controller: MagicMock,
        task_manager_fixture: TaskManager,
        mock_camera_controller: MagicMock,
        sample_scan_model: Scan,
        sample_scan_settings: ScanSetting,
        mock_project_manager: MagicMock,
        sample_camera_metadata: CameraMetadata,
    ):
        """Tests a successful run of the ScanTask using the TaskManager."""
        # Setup service locator mocks
        mock_get_camera_controller.return_value = mock_camera_controller
        mock_get_project_manager.return_value = mock_project_manager
        
        # Mock the path generation to return a simple list of points
        mock_generate_scan_path.return_value = {
            PolarPoint3D(theta=i, fi=i): i for i in range(sample_scan_settings.points)
        }

        # --- Simulate delays in hardware operations ---
        mock_motors.move_to_point.side_effect = async_sleep_side_effect

        def delayed_photo(*args, **kwargs):
            """Simulate a blocking I/O delay for photo capture."""
            time.sleep(0.02)
            return PhotoData(data=io.BytesIO(b"fake_image_bytes"),
                             format="jpeg",
                             camera_metadata=sample_camera_metadata)

        async def delayed_add_photo(*args, **kwargs):
            """Simulate a non-blocking I/O delay for saving a photo."""
            await asyncio.sleep(0.01)

        mock_camera_controller.photo.side_effect = delayed_photo
        mock_project_manager.add_photo_async.side_effect = delayed_add_photo

        tm = task_manager_fixture
        total_expected_steps = sample_scan_settings.points

        # --- Run the task via the manager with new simplified signature ---
        task_model = await tm.create_and_run_task(
            "scan_task",
            sample_scan_model,
            0  # start_from_step
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

        # 4. Verify service locators were called
        mock_get_camera_controller.assert_called_once_with(sample_scan_model.camera_name)
        mock_get_project_manager.assert_called_once()

        mock_project_manager.save_scan_path.assert_called_once_with(
            sample_scan_model,
            mock_generate_scan_path.return_value,
        )

    @pytest.mark.asyncio
    @patch('openscan_firmware.controllers.hardware.cameras.camera.get_camera_controller')
    @patch('openscan_firmware.controllers.services.tasks.core.scan_task.get_project_manager')
    @patch('openscan_firmware.controllers.services.tasks.core.scan_task.generate_scan_path')
    @patch('openscan_firmware.controllers.hardware.motors', create=True)
    async def test_scan_task_error_handling(
        self,
        mock_motors: MagicMock,
        mock_generate_scan_path: MagicMock,
        mock_get_project_manager: MagicMock,
        mock_get_camera_controller: MagicMock,
        task_manager_fixture: TaskManager,
        mock_camera_controller: MagicMock,
        sample_scan_model: Scan,
        mock_project_manager: MagicMock,
    ):
        """Tests that the task handles exceptions during the run and sets status to ERROR."""
        # Setup service locator mocks
        mock_get_camera_controller.return_value = mock_camera_controller
        mock_get_project_manager.return_value = mock_project_manager
        
        mock_generate_scan_path.return_value = {PolarPoint3D(theta=0, fi=0): 1}

        def photo_side_effect(*args, **kwargs):
            raise Exception("Camera error")

        mock_camera_controller.photo.side_effect = photo_side_effect
        # Ensure awaited motors call is awaitable to avoid overshadowing the camera error
        mock_motors.move_to_point = AsyncMock(return_value=None)

        tm = task_manager_fixture

        # --- Run the task ---
        task_model = await tm.create_and_run_task(
            "scan_task",
            sample_scan_model,
            0  # start_from_step
        )

        # --- Wait for completion ---
        final_task_model = await tm.wait_for_task(task_model.id)

        # --- Assertions ---
        assert final_task_model.status == TaskStatus.ERROR
        assert "Camera error" in final_task_model.error

    @pytest.mark.asyncio
    @patch('openscan_firmware.controllers.hardware.cameras.camera.get_camera_controller')
    @patch('openscan_firmware.controllers.services.tasks.core.scan_task.get_project_manager')
    @patch('openscan_firmware.controllers.services.tasks.core.scan_task.generate_scan_path')
    @patch('openscan_firmware.controllers.hardware.motors', create=True)
    async def test_scan_task_cancellation(
        self,
        mock_motors: MagicMock,
        mock_generate_scan_path: MagicMock,
        mock_get_project_manager: MagicMock,
        mock_get_camera_controller: MagicMock,
        task_manager_fixture: TaskManager,
        mock_camera_controller: MagicMock,
        sample_scan_model: Scan,
        sample_camera_metadata: CameraMetadata,
        mock_project_manager: MagicMock,
        caplog,
    ):
        """Tests that a running scan task can be cancelled."""
        # Setup service locator mocks
        mock_get_camera_controller.return_value = mock_camera_controller
        mock_get_project_manager.return_value = mock_project_manager
        
        mock_generate_scan_path.return_value = {PolarPoint3D(theta=i, fi=i): i for i in range(10)}

        # Slow operations to allow cancellation
        async def slow_move(*args, **kwargs):
            await asyncio.sleep(0.1)

        async def slow_add_photo(*args, **kwargs):
            await asyncio.sleep(0.01)

        mock_motors.move_to_point.side_effect = slow_move
        mock_camera_controller.photo.return_value = PhotoData(
            data=io.BytesIO(b"fake_image_bytes"),
            format="jpeg",
            camera_metadata=sample_camera_metadata,
        )
        mock_project_manager.add_photo_async.side_effect = slow_add_photo

        tm = task_manager_fixture

        # --- Start the task ---
        task_model = await tm.create_and_run_task(
            "scan_task",
            sample_scan_model,
            0  # start_from_step
        )

        # Let it run a bit, then cancel
        await asyncio.sleep(0.05)
        cancelled_task = await tm.cancel_task(task_model.id)

        # --- Wait for completion ---
        final_task_model = await tm.wait_for_task(task_model.id)

        # --- Assertions ---
        assert final_task_model.status == TaskStatus.CANCELLED

    @pytest.mark.asyncio
    @patch('openscan_firmware.controllers.hardware.cameras.camera.get_camera_controller')
    @patch('openscan_firmware.controllers.services.tasks.core.scan_task.get_project_manager')
    @patch('openscan_firmware.controllers.services.tasks.core.scan_task.generate_scan_path')
    @patch('openscan_firmware.controllers.hardware.motors', create=True)
    async def test_scan_task_pause_and_resume(
        self,
        mock_motors: MagicMock,
        mock_generate_scan_path: MagicMock,
        mock_get_project_manager: MagicMock,
        mock_get_camera_controller: MagicMock,
        task_manager_fixture: TaskManager,
        mock_camera_controller: MagicMock,
        sample_scan_model: Scan,
        mock_project_manager: MagicMock,
        fake_photo_data: PhotoData,
    ):
        """Tests that a running scan task can be paused and resumed."""
        # Setup service locator mocks
        mock_get_camera_controller.return_value = mock_camera_controller
        mock_get_project_manager.return_value = mock_project_manager
        
        mock_generate_scan_path.return_value = {PolarPoint3D(theta=i, fi=i): i for i in range(5)}

        # Slow operations to allow pause/resume
        async def slow_move_pause(*args, **kwargs):
            await asyncio.sleep(0.1)

        async def slow_add_photo_pause(*args, **kwargs):
            await asyncio.sleep(0.01)

        mock_motors.move_to_point.side_effect = slow_move_pause
        mock_camera_controller.photo.return_value = fake_photo_data
        mock_project_manager.add_photo_async.side_effect = slow_add_photo_pause

        tm = task_manager_fixture

        # --- Start the task ---
        task_model = await tm.create_and_run_task(
            "scan_task",
            sample_scan_model,
            0  # start_from_step
        )

        # Let it run a bit, then pause
        await asyncio.sleep(0.05)
        paused_task = await tm.pause_task(task_model.id)
        assert paused_task.status == TaskStatus.PAUSED

        # Resume after a short pause
        await asyncio.sleep(0.05)
        resumed_task = await tm.resume_task(task_model.id)
        assert resumed_task.status == TaskStatus.RUNNING

        # --- Wait for completion ---
        final_task_model = await tm.wait_for_task(task_model.id)

        # --- Assertions ---
        assert final_task_model.status == TaskStatus.COMPLETED

    @pytest.mark.asyncio
    @patch('openscan_firmware.controllers.hardware.cameras.camera.get_camera_controller')
    @patch('openscan_firmware.controllers.services.tasks.core.scan_task.get_project_manager')
    @patch('openscan_firmware.controllers.services.tasks.core.scan_task.generate_scan_path')
    @patch('openscan_firmware.controllers.hardware.motors', create=True)
    async def test_scan_task_cancel_while_paused(
        self,
        mock_motors: MagicMock,
        mock_generate_scan_path: MagicMock,
        mock_get_project_manager: MagicMock,
        mock_get_camera_controller: MagicMock,
        task_manager_fixture: TaskManager,
        mock_camera_controller: MagicMock,
        sample_scan_model: Scan,
        mock_project_manager: MagicMock,
        fake_photo_data: PhotoData,
    ):
        """Tests that cancelling during a pause transitions the task to CANCELLED."""
        mock_get_camera_controller.return_value = mock_camera_controller
        mock_get_project_manager.return_value = mock_project_manager
        mock_generate_scan_path.return_value = {PolarPoint3D(theta=i, fi=i): i for i in range(5)}

        async def slow_move_pause(*args, **kwargs):
            await asyncio.sleep(0.1)

        async def slow_add_photo_pause(*args, **kwargs):
            await asyncio.sleep(0.01)

        mock_motors.move_to_point.side_effect = slow_move_pause
        mock_camera_controller.photo.return_value = fake_photo_data
        mock_project_manager.add_photo_async.side_effect = slow_add_photo_pause

        tm = task_manager_fixture

        task_model = await tm.create_and_run_task(
            "scan_task",
            sample_scan_model,
            0  # start_from_step
        )

        await asyncio.sleep(0.05)
        paused_task = await tm.pause_task(task_model.id)
        assert paused_task.status == TaskStatus.PAUSED

        cancelled_task = await tm.cancel_task(task_model.id)
        assert cancelled_task.status == TaskStatus.CANCELLED

        final_task_model = await tm.wait_for_task(task_model.id)
        assert final_task_model.status == TaskStatus.CANCELLED

    def test_scan_task_arguments_are_serializable(self, sample_scan_model: Scan):
        """Test that ScanTask arguments can be JSON serialized for persistence."""
        start_from_step = 5
        
        # Should not raise exception
        scan_data = sample_scan_model.model_dump(mode='json')
        json_data = json.dumps([scan_data, start_from_step])
    
        # Verify that the output is a valid JSON string
        assert isinstance(json_data, str)
        loaded_data = json.loads(json_data)
        assert loaded_data[1] == start_from_step

    def test_focus_positions_property(self, sample_scan_settings: ScanSetting):
        """Test that the new focus_positions property works correctly."""
        # Test with no focus stacking
        sample_scan_settings.focus_stacks = 1
        assert sample_scan_settings.focus_positions == []
        
        # Test with focus stacking
        sample_scan_settings.focus_stacks = 3
        sample_scan_settings.focus_range = (10.0, 15.0)
        positions = sample_scan_settings.focus_positions
        
        assert len(positions) == 3
        assert positions[0] == 10.0  # min_focus
        assert positions[-1] == 15.0  # max_focus
        assert positions[1] == 12.5  # middle value


class TestScanTaskIntegration:
    """Integration tests for ScanTask persistence behavior with real ProjectManager."""

    @pytest.mark.asyncio
    @patch('openscan_firmware.controllers.hardware.cameras.camera.get_camera_controller')
    @patch('openscan_firmware.controllers.services.tasks.core.scan_task.get_project_manager')
    @patch('openscan_firmware.controllers.services.tasks.core.scan_task.generate_scan_path')
    @patch('openscan_firmware.controllers.hardware.motors', create=True)
    async def test_scan_json_persistence_integration(
            self,
            mock_motors: MagicMock,
            mock_generate_scan_path: MagicMock,
            mock_get_project_manager: MagicMock,
            mock_get_camera_controller: MagicMock,
            task_manager_fixture: TaskManager,
            mock_camera_controller: MagicMock,
            sample_scan_model: Scan,
            sample_scan_settings: ScanSetting,
            tmp_path,
            fake_photo_data: PhotoData,
    ):
        """Test that scan.json is correctly created and updated during scan execution."""
        # Setup real ProjectManager with temporary directory
        real_project_manager = ProjectManager(path=tmp_path)
        
        # Setup service locator mocks
        mock_get_camera_controller.return_value = mock_camera_controller
        mock_get_project_manager.return_value = real_project_manager

        # Create the project first
        project = real_project_manager.add_project(
            sample_scan_model.project_name,
            "Test project for integration"
        )

        # Add the scan to the project
        scan = real_project_manager.add_scan(
            sample_scan_model.project_name,
            mock_camera_controller,
            sample_scan_settings,
            "Test scan for persistence"
        )

        # Mock the path generation to return a small number of points for faster test
        test_points = 3
        mock_generate_scan_path.return_value = {
            PolarPoint3D(theta=i * 10, fi=i * 10): i for i in range(test_points)
        }

        # Mock hardware operations to be fast
        mock_motors.move_to_point = AsyncMock()
        mock_camera_controller.photo.return_value = fake_photo_data

        # Mock save_scan_state and add_photo_async to avoid file I/O issues
        with patch.object(real_project_manager, 'save_scan_state', new_callable=AsyncMock) as mock_save, \
             patch.object(real_project_manager, 'add_photo_async', new_callable=AsyncMock) as mock_add_photo:

            # Run the scan task
            task_model = await task_manager_fixture.create_and_run_task(
                "scan_task",
                scan,
                0  # start_from_step
            )

            # Wait for completion
            final_task_model = await task_manager_fixture.wait_for_task(task_model.id)

            # Verify task completed successfully
            assert final_task_model.status == TaskStatus.COMPLETED
            assert final_task_model.progress.current == test_points

            # Verify save_scan_state was called (once per step + final save)
            assert mock_save.call_count >= test_points
            
            # Verify add_photo_async was called for each photo
            assert mock_add_photo.call_count == test_points

    @pytest.mark.asyncio
    @patch('openscan_firmware.controllers.hardware.cameras.camera.get_camera_controller')
    @patch('openscan_firmware.controllers.services.tasks.core.scan_task.get_project_manager')
    @patch('openscan_firmware.controllers.services.tasks.core.scan_task.generate_scan_path')
    @patch('openscan_firmware.controllers.hardware.motors', create=True)
    async def test_scan_json_persistence_on_error(
            self,
            mock_motors: MagicMock,
            mock_generate_scan_path: MagicMock,
            mock_get_project_manager: MagicMock,
            mock_get_camera_controller: MagicMock,
            task_manager_fixture: TaskManager,
            mock_camera_controller: MagicMock,
            sample_scan_model: Scan,
            sample_scan_settings: ScanSetting,
            tmp_path,
    ):
        """Ensure scan.json persistence reflects TaskStatus.ERROR when capture fails."""
        real_project_manager = ProjectManager(path=tmp_path)

        mock_get_camera_controller.return_value = mock_camera_controller
        mock_get_project_manager.return_value = real_project_manager

        project = real_project_manager.add_project(
            sample_scan_model.project_name,
            "Test project for error persistence",
        )

        scan = real_project_manager.add_scan(
            sample_scan_model.project_name,
            mock_camera_controller,
            sample_scan_settings,
            "Test scan for error persistence",
        )

        mock_generate_scan_path.return_value = {
            PolarPoint3D(theta=0, fi=0): 0,
        }

        mock_motors.move_to_point = AsyncMock()
        mock_camera_controller.photo.side_effect = RuntimeError("capture failed")

        with patch.object(real_project_manager, 'save_scan_state', new_callable=AsyncMock) as mock_save:
            task_model = await task_manager_fixture.create_and_run_task(
                "scan_task",
                scan,
                0,
            )

            final_task_model = await task_manager_fixture.wait_for_task(task_model.id)

            assert final_task_model.status == TaskStatus.ERROR
            mock_save.assert_awaited()

            saved_scan = mock_save.await_args.args[0]
            assert saved_scan.status == TaskStatus.ERROR
            assert "capture failed" in saved_scan.system_message

    @pytest.mark.asyncio
    @patch('openscan_firmware.controllers.hardware.cameras.camera.get_camera_controller')
    @patch('openscan_firmware.controllers.services.tasks.core.scan_task.get_project_manager')
    @patch('openscan_firmware.controllers.services.tasks.core.scan_task.generate_scan_path')
    @patch('openscan_firmware.controllers.hardware.motors', create=True)
    async def test_scan_json_persistence_with_pause_and_photo_count(
            self,
            mock_motors: MagicMock,
            mock_generate_scan_path: MagicMock,
            mock_get_project_manager: MagicMock,
            mock_get_camera_controller: MagicMock,
            task_manager_fixture: TaskManager,
            mock_camera_controller: MagicMock,
            sample_scan_model: Scan,
            sample_scan_settings: ScanSetting,
            tmp_path,
            fake_photo_data: PhotoData,
    ):
        """Test that scan.json correctly reflects paused status and photo count matches current_step."""
        # Setup real ProjectManager
        real_project_manager = ProjectManager(path=tmp_path)

        # Setup service locator mocks
        mock_get_camera_controller.return_value = mock_camera_controller
        mock_get_project_manager.return_value = real_project_manager

        # Create project and scan
        project = real_project_manager.add_project(
            sample_scan_model.project_name,
            "Test project for pause integration"
        )

        scan = real_project_manager.add_scan(
            sample_scan_model.project_name,
            mock_camera_controller,
            sample_scan_settings,
            "Test scan for pause persistence"
        )

        test_points = 5
        mock_generate_scan_path.return_value =  {
            PolarPoint3D(theta=i * 10, fi=i * 10): i for i in range(test_points)
        }

        # Slower mock operations to allow time for pause
        async def slow_move(*args, **kwargs):
            """Simulate a slow move operation."""
            await asyncio.sleep(0.1)

        def slow_photo(*args, **kwargs):
            """Simulate a slow photo capture."""
            time.sleep(0.05)
            return fake_photo_data

        mock_motors.move_to_point.side_effect = slow_move
        mock_camera_controller.photo.side_effect = slow_photo

        # Mock save_scan_state and add_photo_async to avoid file I/O issues
        with patch.object(real_project_manager, 'save_scan_state', new_callable=AsyncMock) as mock_save, \
             patch.object(real_project_manager, 'add_photo_async', new_callable=AsyncMock) as mock_add_photo:

            # Start the scan
            task_model = await task_manager_fixture.create_and_run_task(
                "scan_task",
                scan,
                0  # start_from_step
            )

            # Let it run for a bit to complete some steps, then pause
            await asyncio.sleep(0.3)
            paused_task = await task_manager_fixture.pause_task(task_model.id)
            assert paused_task.status == TaskStatus.PAUSED

            # Resume and complete
            await task_manager_fixture.resume_task(task_model.id)
            final_task_model = await task_manager_fixture.wait_for_task(task_model.id)

            assert final_task_model.status == TaskStatus.COMPLETED

            # Verify save_scan_state was called multiple times
            assert mock_save.call_count >= test_points

    @pytest.mark.asyncio
    @patch('openscan_firmware.controllers.hardware.cameras.camera.get_camera_controller')
    @patch('openscan_firmware.controllers.services.tasks.core.scan_task.get_project_manager')
    @patch('openscan_firmware.controllers.services.tasks.core.scan_task.generate_scan_path')
    @patch('openscan_firmware.controllers.hardware.motors', create=True)
    async def test_scan_json_persistence_with_focus_stacking(
            self,
            mock_motors: MagicMock,
            mock_generate_scan_path: MagicMock,
            mock_get_project_manager: MagicMock,
            mock_get_camera_controller: MagicMock,
            task_manager_fixture: TaskManager,
            mock_camera_controller: MagicMock,
            sample_scan_model: Scan,
            sample_scan_settings: ScanSetting,
            tmp_path,
            fake_photo_data: PhotoData
    ):
        """Test that focus stacking creates multiple photos per position and persists correctly."""
        # Setup real ProjectManager
        real_project_manager = ProjectManager(path=tmp_path)

        # Setup service locator mocks
        mock_get_camera_controller.return_value = mock_camera_controller
        mock_get_project_manager.return_value = real_project_manager

        # Create project
        project = real_project_manager.add_project(
            sample_scan_model.project_name,
            "Test project for focus stacking"
        )

        # Create scan settings with focus stacking
        focus_scan_settings = sample_scan_settings
        focus_scan_settings.focus_stacks = 3

        # Add scan with focus stacking settings
        scan = real_project_manager.add_scan(
            sample_scan_model.project_name,
            mock_camera_controller,
            focus_scan_settings,
            "Focus stacking test scan"
        )

        # Mock path generation
        test_positions = 2
        mock_generate_scan_path.return_value = {
            PolarPoint3D(theta=i * 45, fi=i * 45): i for i in range(test_positions)
        }

        # Mock hardware
        mock_motors.move_to_point.side_effect = AsyncMock()
        mock_camera_controller.photo.return_value = fake_photo_data
        
        # Mock camera settings for focus stacking
        mock_camera_controller.settings = MagicMock()
        mock_camera_controller.settings.AF = True
        mock_camera_controller.settings.manual_focus = 10.0

        # Mock save_scan_state and add_photo_async to avoid file I/O issues
        with patch.object(real_project_manager, 'save_scan_state', new_callable=AsyncMock) as mock_save, \
             patch.object(real_project_manager, 'add_photo_async', new_callable=AsyncMock) as mock_add_photo:

            # Run scan
            task_model = await task_manager_fixture.create_and_run_task(
                "scan_task", scan, 0  # start_from_step
            )

            # Wait for completion
            final_task_model = await task_manager_fixture.wait_for_task(task_model.id, timeout=10.0)

            # Verify task completed
            assert final_task_model.status == TaskStatus.COMPLETED
            
            # Verify correct number of photos were taken
            # 2 positions × 3 focus stacks = 6 photos total
            expected_photos = test_positions * focus_scan_settings.focus_stacks
            assert mock_camera_controller.photo.call_count == expected_photos
            assert mock_add_photo.call_count == expected_photos

            # Verify focus settings were restored
            assert mock_camera_controller.settings.AF == True  # Should be restored

            # Verify save_scan_state was called
            assert mock_save.call_count >= test_positions