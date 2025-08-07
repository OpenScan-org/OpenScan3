"""
Scan Task


"""
import asyncio
import logging
import time
import os
from fastapi.encoders import jsonable_encoder
from typing import AsyncGenerator, Tuple, Optional, TYPE_CHECKING
from datetime import datetime

from app.config.scan import ScanSetting
from app.controllers.hardware import gpio
from app.controllers.hardware.motors import get_motor_controller
from app.controllers.hardware import motors
from app.controllers.services.projects import ProjectManager
from app.controllers.services.tasks.base_task import BaseTask
from app.controllers.hardware.cameras.camera import CameraController, get_camera_controller
from app.models.camera import Camera
from app.models.paths import CartesianPoint3D, PolarPoint3D, PathMethod
from app.models.scan import Scan, ScanStatus
from app.models.task import TaskProgress
from app.utils.paths import paths
from app.utils.paths.optimization import PathOptimizer
from app.controllers.services.projects import get_project_manager

logger = logging.getLogger(__name__)




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

class ScanTask(BaseTask):

    is_exclusive = True

    async def run(self, scan: Scan, start_from_step: int = 0) -> AsyncGenerator[TaskProgress, None]:
        """Run a scan asynchronously with pause/resume/cancel support

        This method is designed to be used as an async generator. The generator
        will yield `TaskProgress` objects to report the scan's progress.

        The scan can be paused/resumed by calling the pause/resume method on
        the scan manager. The scan can be cancelled by calling the cancel method
        on the scan manager.

        Args:
            scan: The scan object containing settings and persistent state.
            start_from_step: Optional step to start from (for resuming cancelled/failed scans)

        Yields:
            `TaskProgress` objects with progress information.
        """
        # Initialize controllers and generate path
        camera_controller, project_manager = await self._initialize_controllers(scan)
        path = self._generate_scan_path(scan)
        total = len(path)
        logger.info(f"Starting scan {scan.index} for project {scan.project_name} with {total} steps.")

        # Setup photo saving queue
        photo_queue = asyncio.Queue()
        save_task = asyncio.create_task(self._create_photo_saver(photo_queue, project_manager, scan))

        # Filter points for resuming from specific step
        if start_from_step > 0:
            path = path[start_from_step:]

        # Setup focus stacking if needed
        focus_context = await self._setup_focus_stacking(camera_controller, scan)

        try:
            # Execute main scan loop
            async for progress in self._execute_scan_loop(
                scan, path, start_from_step, total, camera_controller, 
                project_manager, photo_queue, focus_context
            ):
                yield progress

        except Exception as e:
            logger.error(f"Error during scan {scan.index} for project {scan.project_name}: {e}", exc_info=True)
            scan.system_message = f"Error during scan: {e}"
            scan.status = ScanStatus.FAILED
            await project_manager.save_scan_state(scan)
            raise

        finally:
            await self._cleanup_scan(
                photo_queue, save_task, project_manager, scan, 
                camera_controller, focus_context
            )

    async def _initialize_controllers(self, scan: Scan) -> Tuple[CameraController, ProjectManager]:
        """Initialize camera controller and project manager
        
        Args:
            scan: The scan object containing camera name
            
        Returns:
            Tuple of (camera_controller, project_manager)
            
        Raises:
            ValueError: If controllers cannot be initialized
        """
        try:
            camera_controller = get_camera_controller(scan.camera_name)
            if not camera_controller:
                raise ValueError(f"Camera '{scan.camera_name}' not found or not available")
            
            project_manager = get_project_manager()
            if not project_manager:
                raise ValueError("ProjectManager not available")
                
            return camera_controller, project_manager
                
        except Exception as e:
            logger.error(f"Failed to initialize controllers: {e}")
            scan.status = ScanStatus.FAILED
            scan.system_message = f"Controller initialization failed: {e}"
            raise

    def _generate_scan_path(self, scan: Scan) -> list[PolarPoint3D]:
        """Generate optimized scan path based on scan settings
        
        Args:
            scan: The scan object containing settings
            
        Returns:
            List of polar points representing the scan path
        """
        return generate_scan_path(scan.settings)

    async def _create_photo_saver(self, photo_queue: asyncio.Queue, 
                                 project_manager: ProjectManager, scan: Scan):
        """Background task for saving photos asynchronously
        
        Args:
            photo_queue: Queue containing photos to save
            project_manager: Manager for saving photos
            scan: Current scan object
        """
        while True:
            try:
                photo, info = await photo_queue.get()
                try:
                    await project_manager.add_photo_async(scan, photo, info)
                except Exception as e:
                    # Log error during save, but don't kill the whole process
                    logger.error(f"Failed to save one photo for scan {scan.id}: {e}", exc_info=True)
                finally:
                    # This is crucial to ensure that join() can complete.
                    photo_queue.task_done()
            except asyncio.CancelledError:
                logger.info("Photo saver task is stopping.")
                break

    async def _setup_focus_stacking(self, camera_controller: CameraController, 
                                   scan: Scan) -> Optional[dict]:
        """Setup focus stacking if enabled in scan settings
        
        Args:
            camera_controller: Camera controller instance
            scan: Scan object with settings
            
        Returns:
            Focus context dict with settings and positions, or None if not enabled
        """
        if scan.settings.focus_stacks <= 1:
            return None

        logger.debug(f"Focus stacking: {scan.settings.focus_stacks}")
        
        # Save focus settings to restore after scanning and turn off autofocus
        previous_focus_settings = (
            camera_controller.settings.AF,
            camera_controller.settings.manual_focus
        )
        camera_controller.settings.AF = False
        logger.debug("Saved focus settings and disabled Autofocus")

        # Use focus positions from scan settings
        focus_positions = scan.settings.focus_positions
        logger.debug(f"Calculated focus positions: {focus_positions}")

        return {
            'enabled': True,
            'previous_settings': previous_focus_settings,
            'positions': focus_positions
        }

    async def _execute_scan_loop(self, scan: Scan, path: list[PolarPoint3D], 
                               start_from_step: int, total: int,
                               camera_controller: CameraController,
                               project_manager: ProjectManager,
                               photo_queue: asyncio.Queue,
                               focus_context: Optional[dict]) -> AsyncGenerator[TaskProgress, None]:
        """Execute the main scan loop
        
        Args:
            scan: Current scan object
            path: List of positions to scan
            start_from_step: Step to start from (for resume)
            total: Total number of steps
            camera_controller: Camera controller instance
            project_manager: Project manager instance
            photo_queue: Queue for saving photos
            focus_context: Focus stacking context or None
            
        Yields:
            TaskProgress objects with current progress
        """
        for index, current_point in enumerate(path):
            step_start_time = datetime.now()
            scan.status = ScanStatus.RUNNING

            # Check for cancellation
            if self.is_cancelled():
                logger.info(f"Scan {scan.index} for project {scan.project_name} was cancelled.")
                scan.status = ScanStatus.CANCELLED
                yield TaskProgress(current=index + start_from_step + 1, total=total, message="Scan cancelled by request.")
                break

            # Wait here if the task is paused
            await self.wait_for_pause()

            # Move to current position
            await motors.move_to_point(current_point)

            # Capture photos (with or without focus stacking)
            photo_info = {"position": index + start_from_step}
            await self._capture_photos_at_position(
                camera_controller, photo_queue, photo_info, 
                current_point, index, focus_context
            )

            # Update scan progress
            scan.duration += (datetime.now() - step_start_time).total_seconds()
            scan.current_step = index + start_from_step + 1

            # Persist the updated scan state
            await project_manager.save_scan_state(scan)

            yield TaskProgress(current=index + start_from_step + 1, total=total, message="Scan in progress.")

        else:  # This block executes if the loop completes without a 'break'
            scan.status = ScanStatus.COMPLETED
            logger.info(f"Scan {scan.index} for project {scan.project_name} completed successfully.")

    async def _capture_photos_at_position(self, camera_controller: CameraController,
                                        photo_queue: asyncio.Queue, photo_info: dict,
                                        current_point: PolarPoint3D, index: int,
                                        focus_context: Optional[dict]):
        """Capture photos at current position with optional focus stacking
        
        Args:
            camera_controller: Camera controller instance
            photo_queue: Queue for saving photos
            photo_info: Base photo information dict
            current_point: Current scan position
            index: Current step index
            focus_context: Focus stacking context or None
        """
        try:
            logger.debug(f"Capturing photo at position {current_point}")
            
            if not focus_context or not focus_context['enabled']:
                # Single photo capture
                photo = camera_controller.photo()
                await photo_queue.put((photo, photo_info.copy()))
            else:
                # Focus stacking capture
                focus_positions = focus_context['positions']
                for stack_index, focus in enumerate(focus_positions):
                    logger.debug(f"Focus stacking enabled, capturing photo {stack_index} / {len(focus_positions)} with focus {focus}")
                    camera_controller.settings.manual_focus = focus
                    photo = camera_controller.photo()
                    stack_photo_info = photo_info.copy()
                    stack_photo_info["stack_index"] = stack_index
                    await photo_queue.put((photo, stack_photo_info))

        except Exception as e:
            logger.error(f"Error taking photo at position {index}: {e}", exc_info=True)
            raise

    async def _cleanup_scan(self, photo_queue: asyncio.Queue, save_task: asyncio.Task,
                          project_manager: ProjectManager, scan: Scan,
                          camera_controller: CameraController, 
                          focus_context: Optional[dict]):
        """Cleanup after scan completion or failure
        
        Args:
            photo_queue: Photo queue to drain
            save_task: Photo saver task to cancel
            project_manager: Project manager for final save
            scan: Current scan object
            camera_controller: Camera controller instance
            focus_context: Focus stacking context or None
        """
        # Wait for all photos to be saved
        logger.debug("Scan routine finished. Waiting for remaining photos to be saved.")
        await photo_queue.join()

        # Stop photo saver task
        logger.debug("All queued photos have been processed. Stopping the photo saver task.")
        save_task.cancel()
        try:
            await save_task
        except asyncio.CancelledError:
            logger.info("Photo saver task has been successfully stopped.")

        # Always save the final state of the scan
        logger.info(f"Saving final state for scan {scan.index} with status {scan.status}.")
        await project_manager.save_scan_state(scan)

        # Motor and camera cleanup
        await self._cleanup_hardware(camera_controller, focus_context)

    async def _cleanup_hardware(self, camera_controller: CameraController, 
                               focus_context: Optional[dict]):
        """Cleanup hardware after scan
        
        Args:
            camera_controller: Camera controller instance
            focus_context: Focus stacking context or None
        """
        try:
            logger.debug("Cleanup after scan...")
            
            # Move motors back to origin position
            await motors.move_to_point(PolarPoint3D(90, 90))
            
            # Restore previous focus settings if focus stacking was enabled
            if focus_context and focus_context['enabled']:
                previous_settings = focus_context['previous_settings']
                if previous_settings:
                    logger.debug("Settings focus settings back to previous settings")
                    camera_controller.settings.AF = previous_settings[0]
                    camera_controller.settings.manual_focus = previous_settings[1]
                    
        except Exception as e:
            logger.error(f"Error during cleanup: {e}", exc_info=True)

def trigger_external_cam(camera: Camera):
    gpio.set_output_pin(camera.external_camera_pin, True)
    time.sleep(camera.external_camera_delay)
    gpio.set_output_pin(camera.external_camera_pin, False)