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

    async def run(self, scan: Scan, camera_controller: CameraController, project_manager: ProjectManager, start_from_step: int = 0) -> AsyncGenerator[TaskProgress, None]:
        """Run a scan asynchronously with pause/resume/cancel support

        This method is designed to be used as an async generator. The generator
        will yield `TaskProgress` objects to report the scan's progress.

        The scan can be paused/resumed by calling the pause/resume method on
        the scan manager. The scan can be cancelled by calling the cancel method
        on the scan manager.

        Args:
            scan: The scan object containing settings and persistent state.
            camera_controller: Camera Controller to use for scanning
            project_manager: The manager responsible for saving scan state.
            start_from_step: Optional step to start from (for resuming cancelled/failed scans)

        Yields:
            `TaskProgress` objects with progress information.
        """
        # Generate optimized scan path
        path = generate_scan_path(scan.settings)
        total = len(path)
        logger.info(f"Starting scan {scan.index} for project {scan.project_name} with {total} steps.")

        # Photo queue for asynchronous save
        photo_queue = asyncio.Queue()

        # Task for saving photos
        async def save_photos():
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

        save_task = asyncio.create_task(save_photos())

        # Filter points for resuming from specific step
        if start_from_step > 0:
            path = path[start_from_step:]

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
                    logger.info(f"Scan {scan.index} for project {scan.project_name} was cancelled.")
                    scan.status = ScanStatus.CANCELLED
                    yield TaskProgress(current=index + start_from_step + 1, total=total, message="Scan cancelled by request.")
                    break

                # Wait here if the task is paused
                await self.wait_for_pause()

                photo_info = {"position": index + start_from_step}

                # move to current position
                await motors.move_to_point(current_point)

                try:
                    logger.debug(f"Capturing photo at position {current_point}")
                    # take photos (with or without focus stacking)
                    if not focus_stacking:
                        photo = camera_controller.photo()
                        await photo_queue.put((photo, photo_info.copy()))
                    else:
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

                # Update duration for this step
                scan.duration += (datetime.now() - step_start_time).total_seconds()
                scan.current_step = index + start_from_step + 1

                # Persist the updated scan state (e.g., current_step)
                await project_manager.save_scan_state(scan)

                yield TaskProgress(current=index + start_from_step + 1, total=total, message="Scan in progress.")

            else:  # This block executes if the loop completes without a 'break'
                scan.status = ScanStatus.COMPLETED
                logger.info(f"Scan {scan.index} for project {scan.project_name} completed successfully.")

        except Exception as e:
            logger.error(f"Error during scan {scan.index} for project {scan.project_name}: {e}", exc_info=True)
            scan.system_message = f"Error during scan: {e}"
            scan.status = ScanStatus.FAILED
            raise  # Re-raise to let the TaskManager know the task failed

        finally:


            logger.debug("Scan routine finished. Waiting for remaining photos to be saved.")
            await photo_queue.join()

            logger.debug("All queued photos have been processed. Stopping the photo saver task.")
            save_task.cancel()
            try:
                await save_task
            except asyncio.CancelledError:
                # This is an expected and clean shutdown.
                logger.info("Photo saver task has been successfully stopped.")

            # Always save the final state of the scan
            logger.info(f"Saving final state for scan {scan.index} with status {scan.status}.")
            await project_manager.save_scan_state(scan)

            # cleanup: move back to origin position and restore settings
            try:
                logger.debug("Cleanup after scan...")
                await motors.move_to_point(PolarPoint3D(90, 90))
                # restore previous focus settings if focus stacking was enabled
                if focus_stacking and previous_focus_settings:
                    logger.debug("Settings focus settings back to previous settings")
                    camera_controller.settings.AF = previous_focus_settings[0]
                    camera_controller.settings.manual_focus = previous_focus_settings[1]
            except Exception as e:
                logger.error(f"Error during cleanup: {e}", exc_info=True)


def trigger_external_cam(camera: Camera):
    gpio.set_output_pin(camera.external_camera_pin, True)
    time.sleep(camera.external_camera_delay)
    gpio.set_output_pin(camera.external_camera_pin, False)