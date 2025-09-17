"""
Scan Task


"""
import asyncio
import logging
import time
import os
from fastapi.encoders import jsonable_encoder
from dataclasses import dataclass
from typing import AsyncGenerator, Tuple, Optional, TYPE_CHECKING
from datetime import datetime

from app.config.scan import ScanSetting

from app.controllers.hardware import gpio
from app.controllers.hardware.motors import get_motor_controller
from app.controllers.hardware import motors
from app.controllers.services.projects import ProjectManager, get_project_manager
from app.controllers.services.tasks.base_task import BaseTask
from app.controllers.hardware.cameras.camera import CameraController, get_camera_controller

from app.models.camera import Camera, PhotoData
from app.models.paths import CartesianPoint3D, PolarPoint3D, PathMethod
from app.models.scan import Scan, ScanStatus, ScanMetadata
from app.models.task import TaskProgress

from app.utils.paths import paths
from app.utils.paths.optimization import PathOptimizer


logger = logging.getLogger(__name__)


def generate_scan_path(scan_settings: ScanSetting) -> dict[PolarPoint3D, int]:
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

    # Save original indices of positions for later use
    path_dict = {pos: i for i, pos in enumerate(path)}


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
        optimized_keys = optimizer.optimize_path(
            list(path_dict.keys()),
            algorithm=scan_settings.optimization_algorithm,
            start_position=start_position
        )
        path_dict = {pos: path_dict[pos] for pos in optimized_keys}

        logger.debug(f"Generated optimized path."
                     f"Used algorithm {scan_settings.optimization_algorithm}")

        return path_dict

    return path_dict


@dataclass
class ScanRuntime:
    scan: Scan
    camera_controller: CameraController
    project_manager: ProjectManager
    path_dict: dict[PolarPoint3D, int]
    focus_context: Optional[dict]


class ScanTask(BaseTask):

    is_exclusive = True
    _ctx: Optional[ScanRuntime] = None

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
        path_dict = generate_scan_path(scan.settings)
        total = len(path_dict)
        logger.info(f"Starting scan {scan.index} for project {scan.project_name} with {total} steps.")

        # Filter positions for resuming from specific step
        if start_from_step > 0:
            keys = list(path_dict.keys())[start_from_step:]
            path_dict = {pos: path_dict[pos] for pos in keys}

        # Setup focus stacking if needed
        focus_context = await self._setup_focus_stacking(camera_controller, scan)

        self._ctx = ScanRuntime(
            scan=scan,
            camera_controller=camera_controller,
            project_manager=project_manager,
            path_dict=path_dict,
            focus_context=focus_context
        )

        try:
            # Execute main scan loop
            async for progress in self._execute_scan_loop(start_from_step, total):
                yield progress

        except Exception as e:
            logger.error(f"Error during scan {scan.index} for project {scan.project_name}: {e}", exc_info=True)
            scan.system_message = f"Error during scan: {e}"
            scan.status = ScanStatus.FAILED
            await self._ctx.project_manager.save_scan_state(scan)
            raise

        finally:
            await self._cleanup_scan()

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
            logger.error(f"Failed to initialize scan and get controllers: {e}")
            scan.status = ScanStatus.FAILED
            scan.system_message = f"Controller initialization failed: {e}"
            raise


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

    async def _execute_scan_loop(self, start_from_step: int, total: int,
                                  ) -> AsyncGenerator[TaskProgress, None]:
        """Execute the main scan loop
        
        Args:
            start_from_step: Step to start from (for resume)
            total: Total number of steps
            
        Yields:
            TaskProgress objects with current progress
        """
        for current_step, current_point in enumerate(self._ctx.path_dict.keys()):
            original_index = self._ctx.path_dict[current_point]
            step_start_time = datetime.now()
            self._ctx.scan.status = ScanStatus.RUNNING

            # Check for cancellation
            if self.is_cancelled():
                logger.info(f"Scan {self._ctx.scan.index} for project {self._ctx.scan.project_name} was cancelled.")
                self._ctx.scan.status = ScanStatus.CANCELLED
                yield TaskProgress(current=current_step + start_from_step + 1, total=total, message="Scan cancelled by request.")
                break

            # Wait here if the task is paused
            await self.wait_for_pause()

            # Move to current position
            await motors.move_to_point(current_point)

            # Capture photos (with or without focus stacking)
            try:
                await self._capture_photos_at_position(current_point, original_index)
            except Exception as e:
                logger.error(f"Error taking photo at position {original_index}: {e}", exc_info=True)
                raise e

            # Update scan progress
            self._ctx.scan.duration += (datetime.now() - step_start_time).total_seconds()
            self._ctx.scan.current_step = current_step + start_from_step + 1

            await self._ctx.project_manager.save_scan_state(self._ctx.scan)

            yield TaskProgress(current=current_step + start_from_step + 1, total=total, message="Scan in progress.")

        else:  # This block executes if the loop completes without a 'break'
            self._ctx.scan.status = ScanStatus.COMPLETED
            logger.info(f"Scan {self._ctx.scan.index} for project {self._ctx.scan.project_name} completed successfully.")
            self._task_model.result = f"Scan completed successfully after {total} steps."
            yield TaskProgress(current=total, total=total, message="Scan completed successfully.")

    async def _capture_photos_at_position(self, current_point: PolarPoint3D, index: int):
        """Capture photos at current position with optional focus stacking
        
        Args:
            current_point: Current scan position
            index: Index of the current original step in path_dict
        """
        try:
            logger.debug(f"Capturing photo at position {current_point}")
            
            if not self._ctx.focus_context or not self._ctx.focus_context['enabled']:
                # Single photo capture
                photo_data = self._ctx.camera_controller.photo(self._ctx.scan.settings.image_format)
                photo_data.scan_metadata = ScanMetadata(
                    step=index,
                    polar_coordinates=current_point,
                    project_name=self._ctx.scan.project_name,
                    scan_index=self._ctx.scan.index
                )

                asyncio.create_task(self._ctx.project_manager.add_photo_async(photo_data))
            else:
                # Focus stacking capture
                focus_positions = self._ctx.focus_context['positions']
                for stack_index, focus in enumerate(focus_positions):
                    logger.debug(f"Focus stacking enabled, capturing photo {stack_index} / {len(focus_positions)} with focus {focus}")
                    self._ctx.camera_controller.settings.manual_focus = focus

                    photo_data = self._ctx.camera_controller.photo(self._ctx.scan.settings.image_format)
                    photo_data.scan_metadata = ScanMetadata(
                        step=index,
                        polar_coordinates=current_point,
                        project_name=self._ctx.scan.project_name,
                        scan_index=self._ctx.scan.index,
                        stack_index=stack_index
                    )

                    asyncio.create_task(self._ctx.project_manager.add_photo_async(photo_data))

        except Exception as e:
            logger.error(f"Error taking photo at position {index}: {e}", exc_info=True)
            raise

    async def _cleanup_scan(self):
        """Cleanup after scan completion or failure and reset focus settings if needed.
        """
        # Always save the final state of the scan
        #logger.info(f"Saving final state for scan {scan.index} with status {scan.status}.")
        #await project_manager.save_scan_state(scan) # TODO: handle persistance

        # Motor and camera cleanup
        try:
            # Move motors back to origin position
            await motors.move_to_point(PolarPoint3D(90, 90))

            # Restore previous focus settings if focus stacking was enabled
            if self._ctx.focus_context and self._ctx.focus_context['enabled']:
                previous_settings = self._ctx.focus_context['previous_settings']
                if previous_settings:
                    logger.debug("Settings focus settings back to previous settings")
                    self._ctx.camera_controller.settings.AF = previous_settings[0]
                    self._ctx.camera_controller.settings.manual_focus = previous_settings[1]

        except Exception as e:
            logger.error(f"Error during cleanup: {e}", exc_info=True)
