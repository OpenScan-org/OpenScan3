"""
Core Crop Task (Proof of Concept)

Blocking task that analyzes a captured image and updates camera crop settings based on
contour analysis. Designed without import-time hardware initialization to remain
safe for autodiscovery.
"""
from __future__ import annotations

import logging
import base64

import cv2
import numpy as np

from app.controllers.services.tasks.base_task import BaseTask

logger = logging.getLogger(__name__)


class CropTask(BaseTask):
    """Crop the camera image based on simple contour analysis.

    This blocking task captures one RGB array from the selected camera and
    performs a contour analysis inspired by the OpenCV tutorial on bounding
    rectangles and circles.

    The task sets the camera's crop settings based on detected
    bounding rectangles and returns a Base64-encoded JPEG that visualizes the
    found contours, rectangles, and circles drawn onto the image.

    The analysis is performed according to this example in the OpenCV documentation:
    https://docs.opencv.org/4.x/da/d0c/tutorial_bounding_rects_circles.html

    Note:
        It is marked as blocking because it runs synchronously in the
        TaskManager's thread pool.
    """

    task_name = "crop_task"
    task_category = "core"
    is_exclusive = False
    is_blocking = True

    def run(self, camera_name: str, threshold: int | None = None) -> dict:
        """Execute the crop analysis and return a visualization image.

        Args:
            camera_name: Name of the camera controller to use.
            threshold: Optional Canny threshold passed to the analysis.

        Returns:
            dict: JSON-serializable result containing the Base64-encoded JPEG
            visualization and computed crop settings, e.g.::

                {
                  "mime": "image/jpeg",
                  "image_base64": "...",
                  "bbox": [x, y, w, h],
                  "crop_width": 10,
                  "crop_height": 12
                }
        """
        logger.debug("Starting crop task for camera: %s", camera_name)

        # a) Initialize and capture an RGB array
        image_rgb, camera_controller = self._initialize_and_capture(camera_name)

        # b) Analyze contours per OpenCV example (optionally with provided threshold)
        vis_image_rgb, object_roi_rect = self._analyze_contours(
            image_rgb,
            threshold=threshold if threshold is not None else 100,
        )

        # c) Apply crop settings based on largest bounding rectangle
        crop_settings = self._apply_crop_settings(camera_controller, object_roi_rect, vis_image_rgb.shape)

        # Encode visualization as JPEG (convert RGB->BGR for OpenCV encoding)
        bgr_for_encode = cv2.cvtColor(vis_image_rgb, cv2.COLOR_RGB2BGR)
        success, buf = cv2.imencode(".jpg", bgr_for_encode)
        if not success:
            raise RuntimeError("Failed to encode result image.")

        img_b64 = base64.b64encode(buf.tobytes()).decode("ascii")

        result = {
            "mime": "image/jpeg",
            "image_base64": img_b64,
            "bbox": [int(object_roi_rect[0]), int(object_roi_rect[1]), int(object_roi_rect[2]), int(object_roi_rect[3])],
            "crop_width": int(crop_settings["crop_width"]),
            "crop_height": int(crop_settings["crop_height"]),
        }

        logger.debug("Crop task completed with settings: %s", crop_settings)
        return result

    # ---- helper methods ----
    def _initialize_and_capture(self, camera_name: str) -> tuple[np.ndarray, object]:
        """Initialize camera controller and capture one RGB array.

        Args:
            camera_name: Name of the camera controller to use.

        Returns:
            tuple[np.ndarray, object]: The captured RGB image array and the camera controller instance.
        """
        # Lazy import to avoid hardware side effects at module import
        from app.controllers.hardware.cameras.camera import get_camera_controller

        controller = get_camera_controller(camera_name)
        photo = controller.capture_rgb_array()  # returns PhotoData with np.ndarray in .data
        image = photo.data

        if image is None or not isinstance(image, np.ndarray):
            raise RuntimeError("Failed to capture RGB array from camera controller.")

        # Ensure it's 3-channel RGB
        if image.ndim != 3 or image.shape[2] != 3:
            raise RuntimeError(f"Unexpected image shape: {image.shape}")

        # Apply orientation based on camera settings so analysis matches user view
        try:
            orientation_flag = int(controller.settings.orientation_flag or 1)
        except Exception:
            orientation_flag = 1
        image_oriented = self._apply_orientation(image, orientation_flag)

        return image_oriented, controller

    def _analyze_contours(
        self,
        src_rgb: np.ndarray,
        threshold: int = 100,
    ) -> tuple[np.ndarray, tuple[int, int, int, int]]:
        """Analyze contours and draw polygons, rectangles, and circles and determine the region of interest (ROI) of the object.

        This follows the OpenCV tutorial closely:
        - Convert to gray and blur
        - Canny edge detection
        - findContours, approxPolyDP, boundingRect, minEnclosingCircle
        - Draw results on a visualization image
        (- Additionally: Union of all bounding rectangles as primary ROI of the object)

        You can find the OpenCV tutorial here: https://docs.opencv.org/4.x/da/d0c/tutorial_bounding_rects_circles.html

        Args:
            src_rgb: Source image in RGB color space.
            threshold: Canny threshold value.

        Returns:
            tuple[np.ndarray, tuple[int, int, int, int]]: Visualization RGB image and
            the ROI rectangle which contains all bounding boxes of the object as (x, y, w, h).
        """
        # Downscale for speed on Raspberry Pi, then follow the tutorial on the downscaled image
        # Keep this factor moderate to preserve shape while improving performance
        scale = 0.3  # tuned for Pi; adjust if needed for speed/accuracy tradeoff
        if 0.1 < scale < 1.0:
            proc = cv2.resize(src_rgb, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        else:
            proc = src_rgb
            scale = 1.0

        # Convert image to gray and blur it (OpenCV example uses blur(3,3))
        src_gray = cv2.cvtColor(proc, cv2.COLOR_RGB2GRAY)
        src_gray = cv2.blur(src_gray, (3, 3))

        # Detect edges using Canny
        canny_output = cv2.Canny(src_gray, threshold, threshold * 2)

        # Find contours (OpenCV example uses RETR_TREE)
        contours, _ = cv2.findContours(canny_output, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)

        # Approximate contours to polygons + get bounding rects and circles
        contours_poly = [None] * len(contours)
        boundRect_ds = [None] * len(contours)
        centers_ds = [None] * len(contours)
        radius_ds = [None] * len(contours)
        for i, c in enumerate(contours):
            contours_poly[i] = cv2.approxPolyDP(c, 3, True)
            boundRect_ds[i] = cv2.boundingRect(contours_poly[i])
            centers_ds[i], radius_ds[i] = cv2.minEnclosingCircle(contours_poly[i])

        # Scale geometry back to original image coordinates for drawing and ROI selection
        inv = 1.0 / scale
        boundRect = []
        centers = []
        radius = []
        scaled_polys = []
        for i in range(len(contours)):
            if boundRect_ds[i] is not None:
                x, y, w, h = boundRect_ds[i]
                boundRect.append((int(x * inv), int(y * inv), int(w * inv), int(h * inv)))
            else:
                boundRect.append(None)
            if centers_ds[i] is not None and radius_ds[i] is not None:
                centers.append((centers_ds[i][0] * inv, centers_ds[i][1] * inv))
                radius.append(radius_ds[i] * inv)
            else:
                centers.append(None)
                radius.append(None)
            # Scale polygon points
            if contours_poly[i] is not None:
                poly = (contours_poly[i] * inv).astype(np.int32)
                scaled_polys.append(poly)
            else:
                scaled_polys.append(None)

        # Create drawing canvas matching the original image size
        H, W = src_rgb.shape[:2]
        drawing = np.zeros((H, W, 3), dtype=np.uint8)

        # Draw polygonal contour + bounding rects + circles
        for i in range(len(contours)):
            color = (
                int((i * 37) % 256),
                int((i * 97) % 256),
                int((i * 157) % 256),
            )
            if scaled_polys[i] is not None:
                cv2.drawContours(drawing, [scaled_polys[i]], -1, color)
            if boundRect[i] is not None:
                x, y, w, h = boundRect[i]
                cv2.rectangle(drawing, (int(x), int(y)), (int(x + w), int(y + h)), color, 2)
            if centers[i] is not None and radius[i] is not None:
                cv2.circle(drawing, (int(centers[i][0]), int(centers[i][1])), int(radius[i]), color, 2)

        # Compute union of all bounding rectangles as primary ROI of the object
        candidate_rects = [r for r in boundRect if r is not None]
        if candidate_rects:
            xmin = min(r[0] for r in candidate_rects)
            ymin = min(r[1] for r in candidate_rects)
            xmax = max(r[0] + r[2] for r in candidate_rects)
            ymax = max(r[1] + r[3] for r in candidate_rects)
            roi_rect = (int(xmin), int(ymin), int(xmax - xmin), int(ymax - ymin))
            # Draw union rect in red with thicker stroke on the RGB drawing
            ux, uy, uw, uh = roi_rect
            cv2.rectangle(drawing, (ux, uy), (ux + uw, uy + uh), (255, 0, 0), thickness=10)
        else:
            h, w = src_rgb.shape[:2]
            roi_rect = (0, 0, w, h)

        return drawing, roi_rect

    def _apply_orientation(self, image_rgb: np.ndarray, orientation_flag: int) -> np.ndarray:
        """Apply EXIF Orientation (1..8) with correct flips and rotations.

        Args:
            image_rgb: Input image in RGB.
            orientation_flag: EXIF orientation flag (1..8).

        Returns:
            np.ndarray: Oriented image.
        """
        # EXIF Orientation mapping:
        # 1 = 0°
        # 2 = Mirrored horizontally
        # 3 = 180°
        # 4 = Mirrored vertically
        # 5 = Mirrored horizontally, then 90° CW
        # 6 = 90° CW
        # 7 = Mirrored horizontally, then 90° CCW
        # 8 = 90° CCW
        flag = int(orientation_flag or 1)
        img = image_rgb
        if flag == 1:
            return img
        elif flag == 2:
            return cv2.flip(img, 1)
        elif flag == 3:
            return cv2.rotate(img, cv2.ROTATE_180)
        elif flag == 4:
            return cv2.flip(img, 0)
        elif flag == 5:
            img = cv2.flip(img, 1)
            return cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
        elif flag == 6:
            return cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
        elif flag == 7:
            img = cv2.flip(img, 1)
            return cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)
        elif flag == 8:
            return cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)
        return img

    def _apply_crop_settings(self, controller: CameraController, roi_rect: tuple[int, int, int, int], image_shape: tuple[int, int, int]) -> dict:
        """Compute and apply crop settings using symmetric crop around union rect (Variant C).

        The project currently supports `crop_width` and `crop_height` in percent
        on the camera settings model. Width/height are defined relative to the
        already oriented image (UI view). We take the union rect of all boxes
        and then construct a symmetric crop by taking, per axis, the minimum
        distance of the union rect to the two borders. This yields a centered
        crop that fully contains the union rect.

        Args:
            controller: Camera controller instance whose settings will be updated.
            roi_rect: Largest bounding rectangle (x, y, w, h).
            image_shape: Shape of the image array (H, W, C).

        Returns:
            dict: The applied settings as a dictionary.
        """
        img_h, img_w = image_shape[0], image_shape[1]
        x, y, w, h = roi_rect

        # Distances from union rect to each border
        left_off = max(0, int(x))
        right_off = max(0, int(img_w - (x + w)))
        top_off = max(0, int(y))
        bottom_off = max(0, int(img_h - (y + h)))

        # symmetric crop using the minimum offset per axis
        pad_x = min(left_off, right_off)
        pad_y = min(top_off, bottom_off)

        sym_w = max(1, int(img_w - 2 * pad_x))
        sym_h = max(1, int(img_h - 2 * pad_y))

        # Convert to percent cropped away from full image
        crop_w_percent = max(0, min(100, int(round((1 - (sym_w / float(img_w))) * 100))))
        crop_h_percent = max(0, min(100, int(round((1 - (sym_h / float(img_h))) * 100))))

        controller.settings.crop_width = crop_w_percent
        controller.settings.crop_height = crop_h_percent

        return {
            "crop_width": crop_w_percent,
            "crop_height": crop_h_percent,
            "union_rect": [int(x), int(y), int(w), int(h)],
            "symmetric_rect": [int(pad_x), int(pad_y), int(sym_w), int(sym_h)],
        }
