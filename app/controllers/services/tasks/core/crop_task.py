"""
Core Crop Task

Blocking task that analyzes a captured image and updates crop settings based on
contour analysis. Designed without import-time hardware initialization to remain
safe for autodiscovery.
"""
from __future__ import annotations

import logging
from io import BytesIO

import cv2
import numpy as np

from app.controllers.services.tasks.base_task import BaseTask

logger = logging.getLogger(__name__)


class CropTask(BaseTask):
    """Crop the camera image based on simple contour analysis.

    This blocking task reads one photo from the configured camera, computes a
    central region-of-interest, performs thresholding and finds the largest
    contour to estimate a tighter crop. It writes crop parameters back to the
    camera settings.

    Note: It is marked blocking because it uses synchronous calls and runs in
    the TaskManager's thread pool when scheduled.
    """

    task_name = "crop_task"
    task_category = "core"
    is_exclusive = False
    is_blocking = True

    def run(self, camera_name: str) -> str:
        """Execute the crop analysis.

        Args:
            camera_name: Name of the camera controller to use.

        Returns:
            Human-readable result message.
        """
        logger.debug("Starting crop task for camera: %s", camera_name)

        # Lazy import to avoid hardware side effects on module import
        from app.controllers.hardware.cameras.camera import get_camera_controller

        camera_controller = get_camera_controller(camera_name)
        jpeg_bytes_stream = camera_controller.photo()

        # Decode the image using OpenCV. The photo() method returns bytes directly.
        image = cv2.imdecode(np.frombuffer(jpeg_bytes_stream, np.uint8), cv2.IMREAD_COLOR)

        if image is None:
            logger.error("Failed to decode image.")
            return "Failed to decode image."

        original_height, original_width, _ = image.shape

        # Define a Region of Interest (ROI) focusing on the center of the image,
        # ignoring a 15% border on all sides to avoid rig edges/noise.
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
            x, y, w, h = cv2.boundingRect(largest_contour)
            x += roi_x
            y += roi_y

            logger.debug("Found %dx%d bounding box at %d,%d", h, w, x, y)

            if w < original_width and h < original_height:
                crop_w_percent = int((1 - (w / original_width)) * 100)
                crop_h_percent = int((1 - (h / original_height)) * 100)

                logger.debug("Cropping by %d%% width and %d%% height.", crop_w_percent, crop_h_percent)

                camera_controller.settings.crop_width = crop_w_percent
                camera_controller.settings.crop_height = crop_h_percent

                return f"Cropping parameters updated: {crop_w_percent}% width, {crop_h_percent}% height."
            else:
                camera_controller.settings.crop_width = 0
                camera_controller.settings.crop_height = 0
                logger.debug("Contour is full image size, skipping crop.")
                return "Contour is full image size, skipping crop."
        else:
            logger.debug("No contour found, skipping crop.")
            return "No contour found, skipping crop."
