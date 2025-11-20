"""
Focus Stacking Task

This module provides a background task for focus stacking images in a scan.
Up to 3 stacking tasks can run concurrently (TaskManager limit).
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import AsyncGenerator

from openscan.controllers.services.tasks.base_task import BaseTask
from openscan.models.task import TaskProgress

logger = logging.getLogger(__name__)


class FocusStackingTask(BaseTask):
    """Process focus stack images from a scan.

    This async task takes all focus-stacked images from a scan directory,
    calibrates alignment transforms, and outputs merged images.
    """

    task_name = "focus_stacking_task"
    task_category = "core"
    is_exclusive = False
    is_blocking = False

    async def run(self, project_name: str, scan_index: int) -> AsyncGenerator[TaskProgress, None]:
        """Execute focus stacking on scan images with progress reporting.

        Args:
            project_name: Name of the project containing the scan
            scan_index: Index of the scan to process

        Yields:
            TaskProgress updates
        """
        from openscan.controllers.services.projects import get_project_manager

        logger.info(f"Starting focus stacking for project '{project_name}', scan {scan_index}")

        # Get project and scan info
        project_manager = get_project_manager()
        project = project_manager.get_project_by_name(project_name)
        if not project:
            raise ValueError(f"Project '{project_name}' not found")

        scan = project_manager.get_scan_by_index(project_name, scan_index)
        if not scan:
            raise ValueError(f"Scan {scan_index} not found in project '{project_name}'")

        # Get stacking parameters from scan settings
        num_calibration_batches = 3

        # Build paths
        scan_dir = Path(project.path) / f"scan{scan.index:02d}"
        output_dir = scan_dir / "stacked"

        if not scan_dir.exists():
            raise ValueError(f"Scan directory not found: {scan_dir}")

        # Check for focus stack images (run in executor since it's I/O bound)
        loop = asyncio.get_running_loop()
        batches = await loop.run_in_executor(None, self._find_batches, str(scan_dir))

        if not batches:
            raise ValueError(f"No focus stack images found in {scan_dir}")

        total_batches = len(batches)
        logger.info(f"Found {total_batches} focus stack batches to process")

        # Yield initial progress
        yield TaskProgress(current=0, total=total_batches, message="Starting calibration...")

        # Calibration phase (CPU-intensive, run in executor)
        logger.info(f"Calibrating with {num_calibration_batches} batches...")
        stacker = await loop.run_in_executor(
            None,
            self._calibrate_stacker,
            str(scan_dir),
            num_calibration_batches
        )
        logger.info("Calibration complete")

        yield TaskProgress(current=0, total=total_batches, message="Calibration complete, starting stacking...")

        # Process all batches
        output_dir.mkdir(exist_ok=True)
        output_paths = []

        for idx, (position, image_paths) in enumerate(sorted(batches.items())):
            await self.wait_for_pause()

            # Check for cancel
            if self.is_cancelled():
                logger.info("Focus stacking cancelled by user")
                yield TaskProgress(current=idx, total=total_batches, message="Cancelled by user")
                return

            # Stack this batch (CPU-intensive, run in executor)
            output_path = output_dir / f"stacked_scan{scan_index:02d}_{position:03d}.jpg"
            await loop.run_in_executor(
                None,
                self._stack_batch,
                stacker,
                image_paths,
                str(output_path)
            )
            output_paths.append(str(output_path))

            logger.debug(f"Stacked batch {idx + 1}/{total_batches} (position {position})")

            # Yield progress update
            yield TaskProgress(
                current=idx + 1,
                total=total_batches,
                message=f"Stacking batch {idx + 1} of {total_batches}"
            )

        logger.info(f"Focus stacking complete: {len(output_paths)} images created in {output_dir}")

        # Set final result
        self._task_model.result = {
            "output_directory": str(output_dir),
            "stacked_image_count": len(output_paths),
            "output_paths": output_paths,
        }

        yield TaskProgress(
            current=total_batches,
            total=total_batches,
            message="Focus stacking complete"
        )

    def _find_batches(self, scan_dir: str) -> dict:
        """Find image batches (blocking I/O)."""
        from openscan.utils.photos.stacking import find_image_batches
        return find_image_batches(scan_dir)

    def _calibrate_stacker(self, scan_dir: str, num_batches: int):
        """Calibrate the stacker (blocking CPU work)."""
        from openscan.utils.photos.stacking import FocusStacker
        stacker = FocusStacker(downscale=0.25, jpeg_quality=90)
        stacker.calibrate_from_directory(scan_dir, num_batches=num_batches)
        return stacker

    def _stack_batch(self, stacker, image_paths: list, output_path: str):
        """Stack a single batch (blocking CPU work)."""
        stacker.stack(image_paths, output_path)