"""
Scan Manager

The Scan Manager is responsible for managing the scan process, including
the scan status, progress, and control. There can only be one active ScanManager
at a time.

"""
import asyncio
import logging
import time
import os
from fastapi.encoders import jsonable_encoder
from typing import AsyncGenerator, Tuple, Optional, TYPE_CHECKING
from datetime import datetime

from config.scan import ScanSetting
from app.controllers.hardware import gpio
from app.controllers.hardware.motors import get_motor_controller
from app.controllers.services.projects import ProjectManager
from app.controllers.hardware.cameras.camera import CameraController, get_camera_controller
from app.models.camera import Camera
from app.models.paths import CartesianPoint3D, PolarPoint3D, PathMethod
from app.models.scan import Scan, ScanStatus
from app.utils.paths import paths
from app.utils.paths.optimization import PathOptimizer

logger = logging.getLogger(__name__)

async def move_to_point(point: PolarPoint3D):
    """Move motors to specified polar coordinates"""
    # Get motor controllers
    turntable = get_motor_controller("turntable")
    rotor = get_motor_controller("rotor")

    # wait until motors are ready
    while turntable.is_busy() or rotor.is_busy():
        logger.debug("Waiting for motors to be ready")
        await asyncio.sleep(0.01)

    # Move both motors concurrently to specified point
    await asyncio.gather(
        turntable.move_to(point.fi),
        rotor.move_to(point.theta)
    )

    logger.debug(f"Moved to {point}")


def generate_scan_path(scan_settings: ScanSetting) -> list[PolarPoint3D]:
    """
    Generate scan path based on settings, with optional optimization

    Args:
        scan_settings: Scan configuration including path method and optimization settings

    Returns:
        List of polar points representing the scan path
    """

    # Generate constrained path
    if scan_settings.path_method == PathMethod.FIBONACCI:
        path = paths.get_constrained_path(
            method=scan_settings.path_method,
            num_points=scan_settings.points,
            min_theta=scan_settings.min_theta,
            max_theta=scan_settings.max_theta
        )
        logger.debug(f"Generated Fibonacci path with {len(path)} points")
    else:
        logger.error(f"Unknown path method {scan_settings.path_method}")
        raise ValueError(f"Path method {scan_settings.path_method} not implemented")


    # Optimize path if requested
    if scan_settings.optimize_path:
        logger.debug(f"Generating optimized path with {len(path)} points")

        # Get motor controllers for optimization parameters
        rotor_controller = get_motor_controller("rotor")
        turntable_controller = get_motor_controller("turntable")

        # Create optimizer with motor parameters
        optimizer = PathOptimizer(
            rotor_spr=rotor_controller.settings.steps_per_rotation,
            rotor_acceleration=rotor_controller.settings.acceleration,
            rotor_max_speed=rotor_controller.settings.max_speed,
            turntable_spr=turntable_controller.settings.steps_per_rotation,
            turntable_acceleration=turntable_controller.settings.acceleration,
            turntable_max_speed=turntable_controller.settings.max_speed
        )

        # Calculate times for comparison
        start_position = PolarPoint3D(
            theta=rotor_controller.model.angle,
            fi=turntable_controller.model.angle,
            r=1.0
        )

        original_time, _ = optimizer.calculate_path_time(path, start_position)

        # Optimize the path
        optimized_path = optimizer.optimize_path(
            path,
            algorithm=scan_settings.optimization_algorithm,
            start_position=start_position
        )
        logger.debug(f"Generated optimized path with {len(optimized_path)} points"
                     f"Used algorithm {scan_settings.optimization_algorithm}")

        return optimized_path

    return path


class ScanManager:
    """Manage scan routines including status, progress and control
    """

    def __init__(self, project_manager: ProjectManager, scan: Scan):
        self._paused = asyncio.Event()
        self._cancelled = asyncio.Event()
        self._paused.set()  # Not paused initially
        self._scan = scan  # Reference to the scan being managed
        self._project_manager = project_manager
        logger.info(f"Initialized scan manager for scan {scan.id} of project {scan.project_name}.")

    def _update_status(self, status: ScanStatus, error_message: Optional[str] = None) -> bool:
        """Update scan status and error message

        Args:
            status: New status to set
            error_message: Optional error message if status is ERROR

        Returns:
            bool: True if status was updated successfully
        """
        try:
            self._scan.status = status
            self._scan.last_updated = datetime.now()
            if error_message and status == ScanStatus.ERROR:
                logger.error(f"Scan error: {error_message}")
                self._scan.system_message = error_message

            if status in [ScanStatus.COMPLETED, ScanStatus.CANCELLED]:
                self._scan.duration = (datetime.now() - self._scan.created).total_seconds()
            logger.info(f"Updated status for scan {self._scan.index} of project '{self._scan.project_name}' to: {status}")
            return True
        except Exception as e:
            logger.error(f"Error updating scan status: {e}", exc_info=True)
            return False

    def update_progress(self, current_step: int, total_steps: int) -> bool:
        """Update scan progress

        Args:
            current_step: Current step number
            total_steps: Total number of steps

        Returns:
            bool: True if progress was updated successfully
        """
        try:
            scan = self._scan

            scan.current_step = current_step
            scan.last_updated = datetime.now()

            if current_step >= total_steps:
                scan.status = ScanStatus.COMPLETED
                scan.duration = (datetime.now() - scan.created).total_seconds()

            logger.info(f"Updated scan progress: {current_step}/{total_steps}")
            return True
        except Exception as e:
            logger.error(f"Error updating scan progress: {e}", exc_info=True)
            return False

    async def pause(self) -> bool:
        """Pause the scan

        Returns:
            bool: True if scan was paused successfully
        """
        try:
            self._paused.clear()
            return self._update_status(ScanStatus.PAUSED)
        except Exception as e:
            logger.error(f"Error pausing scan: {e}", exc_info=True)
            return False

    async def resume(self, camera_controller: CameraController) -> bool:
        """Resume a paused, cancelled or failed scan

        Returns:
            bool: True if scan was resumed successfully
        """
        try:
            # Reset events
            self._cancelled.clear()
            self._paused.set()

            # For scans in cancelled or error state, restart
            if self._scan.status in [ScanStatus.CANCELLED, ScanStatus.ERROR]:
                current_step = self._scan.current_step

                # Restart scan task
                asyncio.create_task(self._run_scan_task(camera_controller, start_from_step=current_step))
                self._update_status(ScanStatus.RUNNING)
                logger.info(f"Restarted scanning task: {self._scan.index} from step {current_step}")
                return True

            # Resume paused scan

            return self._update_status(ScanStatus.RUNNING)

        except Exception as e:
            logger.error(f"Error resuming scan: {e}", exc_info=True)
            self._update_status(ScanStatus.ERROR, str(e))
            return False

    async def cancel(self) -> bool:
        """Cancel the scan

        Returns:
            bool: True if scan was cancelled successfully
        """
        try:
            self._cancelled.set()
            self._paused.set()  # Ensure we're not stuck in pause
            return self._update_status(ScanStatus.CANCELLED)
        except Exception as e:
            logger.error(f"Error cancelling scan: {e}", exc_info=True)
            return False

    async def wait_if_paused(self):
        """Wait if scan is paused"""
        logger.debug("Waiting for scan to be paused...")
        await self._paused.wait()

    def is_cancelled(self) -> bool:
        """Check if scan was cancelled

        Returns:
            bool: True if scan was cancelled
        """
        return self._cancelled.is_set()

    async def start_scan(self, camera_controller: CameraController) -> bool:
        """Start the scan process

        Args:
            camera_controller: Camera Controller to use for the scan

        Returns:
            bool: True if scan was started successfully
        """
        try:
            self._update_status(ScanStatus.RUNNING)
            await self._run_scan_task(camera_controller)
            return True
        except Exception as e:
            self._update_status(ScanStatus.ERROR, str(e))
            logger.error(f"Error starting scan: {e}", exc_info=True)
            return False

    async def _run_scan_task(self, camera_controller: CameraController, start_from_step: int = 0):
        """Internal method to run the scan as a background task

        Args:
            camera_controller: Camera Controller to use for scanning
            start_from_step: Optional step to start from (for resuming cancelled/failed scans)
        """
        try:
            scan_generator = self.scan_async(camera_controller, start_from_step)
            async for step, total in scan_generator:
                self.update_progress(step, total)
        except Exception as e:
            self._update_status(ScanStatus.ERROR, str(e))
            logger.error(f"Error during scan: {e}", exc_info=True)

    async def scan_async(self, camera_controller: CameraController, start_from_step: int = 0) -> AsyncGenerator[
        Tuple[int, int], None]:
        """Run a scan asynchronously with pause/resume/cancel support

        This method is designed to be used as an async generator. The generator
        will yield the current step and total number of steps in the scan.

        The scan can be paused/resumed by calling the pause/resume method on
        the scan manager. The scan can be cancelled by calling the cancel method
        on the scan manager.

        Args:
            camera_controller: Camera Controller to use for scanning
            start_from_step: Optional step to start from (for resuming cancelled/failed scans)

        Yields:
            Tuple[int, int]: Current step and total number of steps in the scan
        """

        scan = self._scan
        project_manager = self._project_manager

        # Generate optimized scan path
        path = generate_scan_path(scan.settings)
        total = len(path)

        # Photo queue for asynchronous save
        photo_queue = asyncio.Queue()

        # Task for saving photos
        async def save_photos():
            while True:
                try:
                    photo, info = await photo_queue.get()
                    await project_manager.add_photo_async(scan, photo, info)
                    photo_queue.task_done()
                except Exception as e:
                    logger.error(f"Error saving photo: {e}", exc_info=True)
                    raise

        save_task = asyncio.create_task(save_photos())

        # Filter points for resuming from specific step
        if start_from_step > 0:
            path = path[start_from_step:]

        next_point = None
        focus_stacking = False

        # prepare focus stacking, if necessary
        if scan.settings.focus_stacks > 1:
            logger.debug(f"Focus stacking: {scan.settings.focus_stacks}")
            focus_stacking = True
            # save focus settings to restore after scanning and turn off autofocus
            previous_focus_settings = (camera_controller.settings.AF,
                                       camera_controller.settings.manual_focus)
            camera_controller.settings.AF = False
            logger.debug(f"Saved focus settings and disabled Autofocus" )

            # Calculate focus positions
            min_focus, max_focus = scan.settings.focus_range
            focus_positions = [
                min_focus + i * (max_focus - min_focus) / (scan.settings.focus_stacks - 1)
                for i in range(scan.settings.focus_stacks)
            ]
            logger.debug(f"Calculated focus positions: {focus_positions}")

        try:
            for index, current_point in enumerate(path):
                step_start_time = datetime.now()

                # Check for cancellation
                if self.is_cancelled():
                    self._update_status(ScanStatus.CANCELLED)
                    yield index + start_from_step, total
                    break

                # Wait if paused
                await self.wait_if_paused()

                photo_info = {"position": index + start_from_step}

                # prepare next coordinate for concurrent movement
                if index < len(path) - 1:
                    next_point = path[index + 1]

                # move to current position
                await move_to_point(current_point)

                # Start moving to next point early if it exists
                move_task = asyncio.create_task(move_to_point(next_point)) if next_point else None

                try:
                    logger.debug(f"Capturing photo at position {current_point}")
                    # take photos (with or without focus stacking)
                    if not focus_stacking:
                        photo = camera_controller.photo()
                        await photo_queue.put((photo, photo_info))
                    else:
                        for stack_index, focus in enumerate(focus_positions):
                            logger.debug(f"Focus stacking enabled, capturing photo with focus {focus}")
                            camera_controller.settings.manual_focus = focus
                            photo = camera_controller.photo()
                            stack_photo_info = photo_info.copy()
                            stack_photo_info["stack_index"] = stack_index
                            await photo_queue.put((photo, stack_photo_info))

                    # Wait for movement to complete if it was started
                    if move_task:
                        await move_task

                except Exception as e:
                    logger.error(f"Error taking photo at position {index}: {e}", exc_info=True)
                    raise

                # Update duration for this step
                scan.duration += (datetime.now() - step_start_time).total_seconds()
                yield index + start_from_step + 1, total

            self._update_status(ScanStatus.COMPLETED)

        except Exception as e:
            self._update_status(ScanStatus.ERROR, str(e))
            logger.error("Scanning error: ", e, exc_info=True)
            raise

        finally:
            logger.debug("Scanning finished")
            await photo_queue.join()
            save_task.cancel()
            try:
                await save_task
            except asyncio.CancelledError:
                logger.error("Scan and saving photos cancelled")

            # cleanup: move back to origin position and restore settings
            try:
                logger.debug("Cleanup after scan...")
                await move_to_point(PolarPoint3D(90, 90))
                # restore previous focus settings if focus stacking was enabled
                if focus_stacking and previous_focus_settings:
                    logger.debug("Settings focus settings back to previous settings")
                    camera_controller.settings.AF = previous_focus_settings[0]
                    camera_controller.settings.manual_focus = previous_focus_settings[1]
            except Exception as e:
                logger.error(f"Error during cleanup: {e}", exc_info=True)


# Create a global scan manager instance that can be accessed by different parts of the application
_active_scan_manager: Optional[ScanManager] = None


def get_scan_manager(scan: Scan, project_manager: ProjectManager) -> ScanManager:
    """Get or create a ScanManager instance for the given scan"""
    global _active_scan_manager

    # If no active manager exists, create a new one
    if _active_scan_manager is None:
        _active_scan_manager = ScanManager(project_manager, scan)
        return _active_scan_manager

    # If the active manager is managing the same scan, return it
    if _active_scan_manager._scan == scan:
        return _active_scan_manager

    # Check the status of the current scan
    current_status = _active_scan_manager._scan.status
    if current_status in [ScanStatus.RUNNING, ScanStatus.PAUSED]:
        logger.error(f"Cannot start new scan: Another scan is {current_status.value}")
        raise RuntimeError(f"Cannot start new scan: Another scan is {current_status.value}")

    # If the current scan is completed, cancelled or failed,
    # we can start a new scan
    if current_status in [ScanStatus.COMPLETED, ScanStatus.CANCELLED, ScanStatus.ERROR]:
        _active_scan_manager = ScanManager(project_manager, scan)
        return _active_scan_manager

    # For all other statuses (e.g. PENDING) we refuse to start a new scan
    logger.error(f"Cannot start new scan: Current scan status is {current_status.value}")
    raise RuntimeError(f"Cannot start new scan: Current scan status is {current_status.value}")


def get_active_scan_manager() -> Optional[ScanManager]:
    """Get the currently active scan manager, if any"""
    return _active_scan_manager


def trigger_external_cam(camera: Camera):
    gpio.set_output_pin(camera.external_camera_pin, True)
    time.sleep(camera.external_camera_delay)
    gpio.set_output_pin(camera.external_camera_pin, False)