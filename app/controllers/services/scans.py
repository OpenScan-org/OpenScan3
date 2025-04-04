import asyncio
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
from app.models.paths import CartesianPoint3D, PolarPoint3D
from app.models.scan import Scan, ScanStatus
from app.services.paths import paths


async def move_to_point(point: paths.PolarPoint3D):
    """Move motors to specified polar coordinates"""
    # Get motor controllers
    turntable = get_motor_controller("turntable")
    rotor = get_motor_controller("rotor")

    # wait until motors are ready
    while turntable.is_busy() or rotor.is_busy():
        await asyncio.sleep(0.01)

    # Move both motors concurrently to specified point
    await asyncio.gather(
        turntable.move_to(point.fi),
        rotor.move_to(point.theta)
    )


class ScanManager:
    """Manage scan routines including status, progress and control"""

    def __init__(self, project_manager: ProjectManager, scan: Scan):
        self._paused = asyncio.Event()
        self._cancelled = asyncio.Event()
        self._paused.set()  # Not paused initially
        self._scan = scan  # Reference to the scan being managed
        self._project_manager = project_manager

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
                print(f"Scan error: {error_message}")
                self._scan.system_message = error_message

            if status in [ScanStatus.COMPLETED, ScanStatus.CANCELLED]:
                self._scan.duration = (datetime.now() - self._scan.created).total_seconds()

            return True
        except Exception as e:
            print(f"Error updating scan status: {e}")
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

            return True
        except Exception as e:
            print(f"Error updating scan progress: {e}")
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
            print(f"Error pausing scan: {e}")
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
                return True

            # Resume paused scan
            return self._update_status(ScanStatus.RUNNING)

        except Exception as e:
            print(f"Error resuming scan: {e}")
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
            print(f"Error cancelling scan: {e}")
            return False

    async def wait_if_paused(self):
        """Wait if scan is paused"""
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
            print(f"Error starting scan: {e}")
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
            print(f"Error during scan: {e}")


    async def scan_async(self, camera_controller: CameraController, start_from_step: int = 0) -> AsyncGenerator[Tuple[int, int], None]:
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

        path = paths.get_path(scan.settings.path_method, scan.settings.points)
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
                    print(f"Error saving photo: {e}")
                    raise

        save_task = asyncio.create_task(save_photos())

        # remove points before start_from_step
        path = path[start_from_step:]

        next_point = None
        focus_stacking = False

        # prepare focus stacking, if necessary
        if scan.settings.focus_stacks > 1:
            focus_stacking = True
            # save focus settings to restore after scanning and turn off autofocus
            previous_focus_settings = (camera_controller.settings.AF,
                                       camera_controller.settings.manual_focus)
            camera_controller.settings.AF = False

            # Calculate focus positions
            min_focus, max_focus = scan.settings.focus_range
            focus_positions = [
                min_focus + i * (max_focus - min_focus) / (scan.settings.focus_stacks - 1)
                for i in range(scan.settings.focus_stacks)
            ]

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

                # prepare next coordinate
                if index < total - 1:
                    next_point = paths.cartesian_to_polar(path[index + 1])

                # current position
                current_polar = paths.cartesian_to_polar(current_point)

                # move to current position
                await move_to_point(current_polar)

                # Start moving to next point early if it exists
                move_task = asyncio.create_task(move_to_point(next_point)) if next_point else None

                try:
                    # take photos (with or without focus stacking)
                    if not focus_stacking:
                        photo = camera_controller.photo()
                        await photo_queue.put((photo, photo_info))
                    else:
                        for stack_index, focus in enumerate(focus_positions):
                            camera_controller.settings.manual_focus = focus
                            photo = camera_controller.photo()
                            stack_photo_info = photo_info.copy()
                            stack_photo_info["stack_index"] = stack_index
                            await photo_queue.put((photo, stack_photo_info))
                            print(f"focus stacking {stack_index}")

                    # Wait for movement to complete if it was started
                    if move_task:
                        await move_task

                except Exception as e:
                    print(f"Error taking photo at position {index}: {e}")
                    raise

                # Update duration for this step
                scan.duration += (datetime.now() - step_start_time).total_seconds()
                yield index + start_from_step + 1, total

            self._update_status(ScanStatus.COMPLETED)

        except Exception as e:
            self._update_status(ScanStatus.ERROR, str(e))
            print("Scanning error: ", e)
            raise

        finally:
            await photo_queue.join()
            save_task.cancel()
            try:
                await save_task
            except asyncio.CancelledError:
                pass

            # cleanup: move back to origin position and restore settings
            try:
                await move_to_point(PolarPoint3D(0, 0))
                # restore previous focus settings if focus stacking was enabled
                if focus_stacking and previous_focus_settings:
                    camera_controller.settings.AF = previous_focus_settings[0]
                    camera_controller.settings.manual_focus = previous_focus_settings[1]
            except Exception as e:
                print(f"Error during cleanup: {e}")


# Create a global scan manager instance that can be accessed by different parts of the application
_active_manager: Optional[ScanManager] = None


def get_scan_manager(scan: Scan, project_manager: ProjectManager) -> ScanManager:
    """Get or create a ScanManager instance for the given scan"""
    global _active_manager

    # If no active manager exists, create a new one
    if _active_manager is None:
        _active_manager = ScanManager(project_manager, scan)
        return _active_manager

    # If the active manager is managing the same scan, return it
    if _active_manager._scan == scan:
        return _active_manager

    # Check the status of the current scan
    current_status = _active_manager._scan.status
    if current_status in [ScanStatus.RUNNING, ScanStatus.PAUSED]:
        raise RuntimeError(f"Cannot start new scan: Another scan is {current_status.value}")

    # If the current scan is completed, cancelled or failed,
    # we can start a new scan
    if current_status in [ScanStatus.COMPLETED, ScanStatus.CANCELLED, ScanStatus.ERROR]:
        _active_manager = ScanManager(project_manager, scan)
        return _active_manager

    # For all other statuses (e.g. PENDING) we refuse to start a new scan
    raise RuntimeError(f"Cannot start new scan: Current scan status is {current_status.value}")


def get_active_scan_manager() -> Optional[ScanManager]:
    """Get the currently active scan manager, if any"""
    return _active_manager


def trigger_external_cam(camera: Camera):
    gpio.set_output_pin(camera.external_camera_pin, True)
    time.sleep(camera.external_camera_delay)
    gpio.set_output_pin(camera.external_camera_pin, False)