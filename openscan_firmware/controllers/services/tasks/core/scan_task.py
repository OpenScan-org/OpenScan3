"""
Core Scan Task

This module provides the production scan task implementation for OpenScan3.
It is structured to avoid import-time side effects, especially hardware
initialization, to remain safe for dynamic autodiscovery.

Public classes and functions use Google-style docstrings.
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import AsyncGenerator, Optional, Tuple

from PIL import Image

from openscan_firmware.config.scan import ScanSetting
from openscan_firmware.controllers.services.projects import ProjectManager, get_project_manager
from openscan_firmware.controllers.services.tasks.base_task import BaseTask
from openscan_firmware.models.paths import PolarPoint3D, PathMethod
from openscan_firmware.models.scan import Scan, ScanMetadata
from openscan_firmware.models.task import TaskProgress, TaskStatus
from openscan_firmware.utils.paths import paths
from openscan_firmware.utils.paths.optimization import PathOptimizer

logger = logging.getLogger(__name__)


def generate_scan_path(scan_settings: ScanSetting) -> dict[PolarPoint3D, int]:
    """Generate scan path based on settings with optional optimization.

    Args:
        scan_settings: Scan configuration including path method and optimization settings.

    Returns:
        Mapping of PolarPoint3D to original index in the un-optimized path.
    """
    # Generate constrained path
    if scan_settings.path_method == PathMethod.FIBONACCI:
        path = paths.get_constrained_path(
            method=scan_settings.path_method,
            num_points=scan_settings.points,
            min_theta=scan_settings.min_theta,
            max_theta=scan_settings.max_theta,
        )
        logger.debug("Generated Fibonacci path with %d points", len(path))
    else:
        logger.error("Unknown path method %s", scan_settings.path_method)
        raise ValueError(f"Path method {scan_settings.path_method} not implemented")

    # Save original indices of positions for later use
    path_dict = {pos: i for i, pos in enumerate(path)}

    # Optimize path if requested
    if scan_settings.optimize_path:
        logger.debug("Generating optimized path with %d points", len(path))

        # Lazy import to avoid hardware side effects on module import
        from openscan_firmware.controllers.hardware.motors import get_motor_controller

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
            turntable_max_speed=turntable_controller.settings.max_speed,
        )

        # Calculate times for comparison
        start_position = PolarPoint3D(
            theta=rotor_controller.model.angle,
            fi=turntable_controller.model.angle,
            r=1.0,
        )

        _original_time, _ = optimizer.calculate_path_time(path, start_position)

        # Optimize the path
        optimized_keys = optimizer.optimize_path(
            list(path_dict.keys()),
            algorithm=scan_settings.optimization_algorithm,
            start_position=start_position,
        )
        path_dict = {pos: path_dict[pos] for pos in optimized_keys}

        logger.debug(
            "Generated optimized path using algorithm %s",
            scan_settings.optimization_algorithm,
        )

        return path_dict

    return path_dict


@dataclass
class ScanRuntime:
    """Runtime aggregation for a running scan."""
    scan: Scan
    project_manager: ProjectManager
    path_dict: dict[PolarPoint3D, int]
    focus_context: Optional[dict]
    # camera_controller is typed as Any to avoid importing camera types at import-time
    camera_controller: object


class ScanTask(BaseTask):
    """Performs the core scan workflow.

    This task controls motors and camera to capture photos along a path. It is
    exclusive because it requires sole access to motors and the camera.
    """

    task_name = "scan_task"
    task_category = "core"
    is_exclusive = True

    async def run(self, scan: Scan, start_from_step: int = 0) -> AsyncGenerator[TaskProgress, None]:
        """Run a scan asynchronously with pause/resume/cancel support.

        Args:
            scan: The scan object containing settings and persistent state.
            start_from_step: Optional step to start from (for resuming cancelled/failed scans).

        Yields:
            TaskProgress objects describing current progress.
        """
        # Initialize controllers and generate path
        camera_controller, project_manager = await self._initialize_controllers(scan)
        await self._ensure_project_thumbnail(camera_controller, project_manager, scan.project_name)
        path_dict = generate_scan_path(scan.settings)
        total = len(path_dict)
        logger.info(
            "Starting scan %s for project %s with %d steps.",
            scan.index,
            scan.project_name,
            total,
        )

        await asyncio.to_thread(project_manager.save_scan_path, scan, path_dict)

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
            focus_context=focus_context,
        )

        try:
            # Execute main scan loop
            async for progress in self._execute_scan_loop(start_from_step, total):
                yield progress
        except Exception as e:
            logger.error(
                "Error during scan %s for project %s: %s",
                scan.index,
                scan.project_name,
                e,
                exc_info=True,
            )
            scan.system_message = f"Error during scan: {e}"
            scan.status = TaskStatus.ERROR
            await self._ctx.project_manager.save_scan_state(scan)
            raise
        finally:
            await self._cleanup_scan()

    async def _initialize_controllers(self, scan: Scan) -> Tuple[object, ProjectManager]:
        """Initialize camera controller and project manager.

        Args:
            scan: The scan object containing camera name.

        Returns:
            Tuple of (camera_controller, project_manager).

        Raises:
            ValueError: If controllers cannot be initialized.
        """
        try:
            # Lazy import to avoid hardware side effects on module import
            from openscan_firmware.controllers.hardware.cameras.camera import get_camera_controller

            camera_controller = get_camera_controller(scan.camera_name)
            if not camera_controller:
                raise ValueError(f"Camera '{scan.camera_name}' not found or not available")

            project_manager = get_project_manager()
            if not project_manager:
                raise ValueError("ProjectManager not available")

            return camera_controller, project_manager
        except Exception as e:
            logger.error("Failed to initialize scan and get controllers: %s", e)
            scan.status = TaskStatus.ERROR
            scan.system_message = f"Controller initialization failed: {e}"
            raise

    async def _setup_focus_stacking(self, camera_controller: object, scan: Scan) -> Optional[dict]:
        """Setup focus stacking if enabled in scan settings.

        Args:
            camera_controller: Camera controller instance.
            scan: Scan object with settings.

        Returns:
            Focus context dict with settings and positions, or None if not enabled.
        """
        if scan.settings.focus_stacks <= 1:
            return None

        logger.debug("Focus stacking: %s", scan.settings.focus_stacks)

        # Save focus settings to restore after scanning and turn off autofocus
        previous_focus_settings = (
            camera_controller.settings.AF,
            camera_controller.settings.manual_focus,
        )
        camera_controller.settings.AF = False
        logger.debug("Saved focus settings and disabled Autofocus")

        # Use focus positions from scan settings
        focus_positions = scan.settings.focus_positions
        logger.debug("Calculated focus positions: %s", focus_positions)

        return {
            "enabled": True,
            "previous_settings": previous_focus_settings,
            "positions": focus_positions,
        }

    async def _execute_scan_loop(self, start_from_step: int, total: int) -> AsyncGenerator[TaskProgress, None]:
        """Execute the main scan loop.

        Args:
            start_from_step: Step to start from (for resume).
            total: Total number of steps.

        Yields:
            TaskProgress objects with current progress.
        """
        # Lazy import to avoid hardware side effects on module import
        from openscan_firmware.controllers.hardware import motors

        for current_step, current_point in enumerate(self._ctx.path_dict.keys()):
            original_index = self._ctx.path_dict[current_point]
            step_start_time = datetime.now()
            self._ctx.scan.status = TaskStatus.RUNNING

            # Check for cancellation
            if self.is_cancelled():
                logger.info(
                    "Scan %s for project %s was cancelled.",
                    self._ctx.scan.index,
                    self._ctx.scan.project_name,
                )
                self._ctx.scan.status = TaskStatus.CANCELLED
                yield TaskProgress(
                    current=current_step + start_from_step + 1,
                    total=total,
                    message="Scan cancelled by request.",
                )
                break

            # Wait here if the task is paused
            await self.wait_for_pause()
            if self.is_cancelled():
                logger.info(
                    "Scan %s for project %s was cancelled while paused.",
                    self._ctx.scan.index,
                    self._ctx.scan.project_name,
                )
                self._ctx.scan.status = TaskStatus.CANCELLED
                yield TaskProgress(
                    current=current_step + start_from_step + 1,
                    total=total,
                    message="Scan cancelled by request.",
                )
                break

            # Move to current position
            await motors.move_to_point(current_point)

            # Capture photos (with or without focus stacking)
            try:
                await self._capture_photos_at_position(current_point, original_index)
            except Exception as e:
                logger.error("Error taking photo at position %s: %s", original_index, e, exc_info=True)
                raise

            # Update scan progress
            self._ctx.scan.duration += (datetime.now() - step_start_time).total_seconds()
            self._ctx.scan.current_step = current_step + start_from_step + 1

            await self._ctx.project_manager.save_scan_state(self._ctx.scan)

            yield TaskProgress(
                current=current_step + start_from_step + 1,
                total=total,
                message="Scan in progress.",
            )
        else:
            # Loop completed without break
            self._ctx.scan.status = TaskStatus.COMPLETED
            logger.info(
                "Scan %s for project %s completed successfully.",
                self._ctx.scan.index,
                self._ctx.scan.project_name,
            )
            self._task_model.result = f"Scan completed successfully after {total} steps."
            yield TaskProgress(current=total, total=total, message="Scan completed successfully.")

    async def _ensure_project_thumbnail(self, camera_controller: object, project_manager: ProjectManager, project_name: str) -> None:
        project = project_manager.get_project_by_name(project_name)
        os.makedirs(project.path, exist_ok=True)
        thumbnail_path = os.path.join(project.path, "thumbnail.jpg")
        if os.path.exists(thumbnail_path):
            return

        preview_bytes = camera_controller.preview()
        orientation_flag = int(camera_controller.settings.orientation_flag or 1)

        await asyncio.to_thread(
            self._save_thumbnail_jpeg,
            preview_bytes,
            thumbnail_path,
            orientation_flag,
        )

    @staticmethod
    def _save_thumbnail_jpeg(preview_bytes: bytes, thumbnail_path: str, orientation_flag: int) -> None:
        image = Image.open(io.BytesIO(preview_bytes))
        image = image.convert("RGB")
        image = ScanTask._apply_orientation_pillow(image, orientation_flag)
        image.thumbnail((512, 512), Image.Resampling.LANCZOS)
        image.save(thumbnail_path, format="JPEG", quality=85, optimize=True)

    @staticmethod
    def _apply_orientation_pillow(image: Image.Image, orientation_flag: int) -> Image.Image:
        flag = int(orientation_flag or 1)
        if flag == 1:
            return image
        if flag == 2:
            return image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        if flag == 3:
            return image.transpose(Image.Transpose.ROTATE_180)
        if flag == 4:
            return image.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        if flag == 5:
            image = image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
            return image.transpose(Image.Transpose.ROTATE_270)
        if flag == 6:
            return image.transpose(Image.Transpose.ROTATE_270)
        if flag == 7:
            image = image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
            return image.transpose(Image.Transpose.ROTATE_90)
        if flag == 8:
            return image.transpose(Image.Transpose.ROTATE_90)
        return image

    async def _capture_photos_at_position(self, current_point: PolarPoint3D, index: int) -> None:
        """Capture photos at current position with optional focus stacking.

        Args:
            current_point: Current scan position.
            index: Index of the current original step in path_dict.
        """
        try:
            logger.debug("Capturing photo at position %s", current_point)

            if not self._ctx.focus_context or not self._ctx.focus_context["enabled"]:
                # Single photo capture
                photo_data = self._ctx.camera_controller.photo(self._ctx.scan.settings.image_format)
                photo_data.scan_metadata = ScanMetadata(
                    step=index,
                    polar_coordinates=current_point,
                    project_name=self._ctx.scan.project_name,
                    scan_index=self._ctx.scan.index,
                )

                asyncio.create_task(self._ctx.project_manager.add_photo_async(photo_data))
            else:
                # Focus stacking capture
                focus_positions = self._ctx.focus_context["positions"]
                for stack_index, focus in enumerate(focus_positions):
                    logger.debug(
                        "Focus stacking enabled, capturing photo %d / %d with focus %s",
                        stack_index,
                        len(focus_positions),
                        focus,
                    )
                    self._ctx.camera_controller.settings.manual_focus = focus

                    photo_data = self._ctx.camera_controller.photo(self._ctx.scan.settings.image_format)
                    photo_data.scan_metadata = ScanMetadata(
                        step=index,
                        polar_coordinates=current_point,
                        project_name=self._ctx.scan.project_name,
                        scan_index=self._ctx.scan.index,
                        stack_index=stack_index,
                    )

                    asyncio.create_task(self._ctx.project_manager.add_photo_async(photo_data))

        except Exception as e:
            logger.error("Error taking photo at position %s: %s", index, e, exc_info=True)
            raise

    async def _cleanup_scan(self) -> None:
        """Cleanup after scan completion or failure and reset focus settings if needed."""
        # Lazy import to avoid hardware side effects on module import
        from openscan_firmware.controllers.hardware import motors

        try:
            # Move motors back to origin position
            await motors.move_to_point(PolarPoint3D(90, 90))

            # Restore previous focus settings if focus stacking was enabled
            if self._ctx.focus_context and self._ctx.focus_context["enabled"]:
                previous_settings = self._ctx.focus_context["previous_settings"]
                if previous_settings:
                    logger.debug("Restoring previous focus settings")
                    self._ctx.camera_controller.settings.AF = previous_settings[0]
                    self._ctx.camera_controller.settings.manual_focus = previous_settings[1]
        except Exception as e:
            logger.error("Error during cleanup: %s", e, exc_info=True)
