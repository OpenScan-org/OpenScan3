"""
Crop Task - Proof of Concept

This task crops the image of the camera according to contour analysis.

This task can be used as a template to create your own tasks.

The task is not exclusive and can run concurrently with other tasks.
The task is blocking and will not proceed until it is resumed. This
also means it is not an async function and thus allegedly easier to
use as a template for your own tasks.

Example usage:

    1. Register the task with the TaskManager:
        task_manager.register_task("crop_task", CropTask)

    2. Use the task in a route:
        @app.post("/crop")
        async def crop(camera_name: str):
            task_manager = get_task_manager()
            task = await task_manager.create_and_run_task("crop_task", camera_name)
            return task

Current implementation:
   - Task registered in main.py
   - Implemented in api endpoint "/tasks/crop" in routers/tasks.py
"""

import asyncio
import logging
from io import BytesIO

import cv2
import numpy as np

from app.controllers.hardware.cameras.camera import get_camera_controller
from app.controllers.services.tasks.base_task import BaseTask
from app.models.task import TaskStatus, TaskProgress

logger = logging.getLogger(__name__)

class CropTask(BaseTask):
    """
    Crop Task

    This tasks crops the image of the camera according to contour analysis.
    This task crops the image to focus on the center of the image, ignoring a 15% border on all sides.
    This helps to avoid noise or unwanted objects at the edges like parts of the scanner rig.
    """
    is_exclusive = False
    is_blocking = True

    def run(self, camera_name: str):
        logger.debug(f"Starting crop task for camera: {camera_name}")

        camera_controller = get_camera_controller(camera_name)
        jpeg_bytes_stream = camera_controller.photo()

        # Decode the image using OpenCV. The photo() method returns bytes directly.
        image = cv2.imdecode(np.frombuffer(jpeg_bytes_stream, np.uint8), cv2.IMREAD_COLOR)

        if image is None:
            logger.error("Failed to decode image.")
            return "Failed to decode image."

        original_height, original_width, _ = image.shape

        # Define a Region of Interest (ROI) to focus on the center of the image,
        # ignoring a 15% border on all sides. This helps to avoid noise or
        # unwanted objects at the edges like parts of the scanner itself like the turntable.
        border_percentage = 0.15
        roi_x = int(original_width * border_percentage)
        roi_y = int(original_height * border_percentage)
        roi_w = int(original_width * (1 - 2 * border_percentage))
        roi_h = int(original_height * (1 - 2 * border_percentage))
        roi = image[roi_y:roi_y + roi_h, roi_x:roi_x + roi_w]

        # Process the ROI instead of the full image
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        _, thresh = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        if contours:
            largest_contour = max(contours, key=cv2.contourArea)

            # Bounding box coordinates are relative to the ROI, so we translate them back to full image coordinates.
            x, y, w, h = cv2.boundingRect(largest_contour)
            x += roi_x
            y += roi_y

            logger.debug(f"Found {h}x{w} bounding box at {x},{y}")

            # Check if the found contour is smaller than the original image
            if w < original_width and h < original_height:
                # Calculate crop percentages as integers (0-100) as required by the settings model.
                crop_w_percent = int((1 - (w / original_width)) * 100)
                crop_h_percent = int((1 - (h / original_height)) * 100)

                logger.debug(f"Cropping by {crop_w_percent}% width and {crop_h_percent}% height.")

                camera_controller.settings.crop_width = crop_w_percent
                camera_controller.settings.crop_height = crop_h_percent

                return f"Cropping parameters updated: {crop_w_percent}% width, {crop_h_percent}% height."
            else:
                # If the bounding box is the size of the image, no cropping is needed.
                camera_controller.settings.crop_width = 0
                camera_controller.settings.crop_height = 0
                logger.debug("Contour is full image size, skipping crop.")
                return "Contour is full image size, skipping crop."
        else:
            logger.debug("No contour found, skipping crop.")
            return "No contour found, skipping crop."
