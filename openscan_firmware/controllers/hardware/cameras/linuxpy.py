"""
LinuxPy camera controller.

LinuxPy exposes a lightweight V4L2 wrapper. Unlike the Picamera2 controller we
cannot provide advanced hardware controls (AF/AWB lock, scaler cropping,
RAW/DNG output). To keep the API stable we derive unsupported formats from the
best-effort JPEG stream and warn the user whenever a fallback path is used.
"""

import io
import logging
from typing import IO, Tuple

import cv2  # type: ignore[import]
import numpy as np
from linuxpy.video.device import Device, VideoCapture  # type: ignore[import]

from openscan_firmware.config.camera import CameraSettings
from openscan_firmware.models.camera import Camera, CameraMetadata, PhotoData

from .camera import CameraController

logger = logging.getLogger(__name__)


class LINUXPYCamera(CameraController):
    """LinuxPy implementation of the generic camera controller."""

    _JPEG_PIXEL_FORMAT = "MJPG"
    _DEFAULT_PREVIEW_RESOLUTION: Tuple[int, int] = (640, 480)
    _DEFAULT_PHOTO_RESOLUTION: Tuple[int, int] = (1920, 1080)

    def __init__(self, camera: Camera):
        if camera.settings is None:
            camera.settings = CameraSettings()
        super().__init__(camera)
        logger.info("Initialized LinuxPy controller for camera '%s'.", camera.name)

    def _apply_settings_to_hardware(self, settings: CameraSettings) -> None:
        """Persist latest settings (LinuxPy exposes only limited controls)."""
        logger.debug(
            "LinuxPy settings updated for '%s': %s",
            self.camera.name,
            settings.model_dump_json(),
        )

    def preview(self) -> IO[bytes]:
        """Capture a low-latency preview frame as JPEG bytes."""
        frame_bytes = self._capture_frame(self._get_preview_resolution())
        return frame_bytes

    def capture_rgb_array(self) -> PhotoData:
        """Capture a frame decoded into an RGB numpy array."""
        rgb_array = self._capture_rgb_array()
        return self._create_photo_data(rgb_array, "rgb_array")

    def capture_yuv_array(self) -> PhotoData:
        """Return a YUV array derived from the captured RGB data."""
        logger.warning(
            "LinuxPy cannot provide native YUV buffers; deriving from RGB frame."
        )
        rgb_array = self._capture_rgb_array()
        yuv_array = cv2.cvtColor(rgb_array, cv2.COLOR_RGB2YUV)
        return self._create_photo_data(yuv_array, "yuv_array")

    def capture_dng(self) -> PhotoData:
        """Return a pseudo DNG payload derived from RGB data."""
        logger.warning(
            "LinuxPy cannot output true RAW/DNG frames; returning RGB byte dump."
        )
        rgb_array = self._capture_rgb_array()
        raw_stream = io.BytesIO(rgb_array.tobytes())
        return self._create_photo_data(raw_stream, "dng")

    def capture_jpeg(self) -> PhotoData:
        """Capture a JPEG frame using the configured photo resolution."""
        jpeg_bytes = self._capture_frame(self._get_photo_resolution())
        jpeg_stream = io.BytesIO(jpeg_bytes)
        return self._create_photo_data(jpeg_stream, "jpeg")

    def _capture_rgb_array(self) -> np.ndarray:
        """Capture a JPEG frame and decode it into an RGB array."""
        jpeg_bytes = self._capture_frame(self._get_photo_resolution())
        np_buffer = np.frombuffer(jpeg_bytes, dtype=np.uint8)
        bgr_image = cv2.imdecode(np_buffer, cv2.IMREAD_COLOR)
        if bgr_image is None:
            raise RuntimeError("LinuxPy failed to decode JPEG stream.")
        return cv2.cvtColor(bgr_image, cv2.COLOR_BGR2RGB)

    def _capture_frame(self, resolution: Tuple[int, int]) -> bytes:
        """Capture a single JPEG frame with the given resolution."""
        width, height = resolution
        self._set_busy(True)
        try:
            with Device(self.camera.path) as device:
                capture = VideoCapture(device)
                capture.set_format(width, height, self._JPEG_PIXEL_FORMAT)
                with capture:
                    frame_iter = iter(capture)
                    next(frame_iter, None)  # discard first frame (often garbage)
                    frame = next(frame_iter)
                    return bytes(frame)
        except StopIteration as exc:
            raise RuntimeError("LinuxPy did not provide a frame.") from exc
        finally:
            self._set_busy(False)

    def _create_photo_data(self, data, data_format: str) -> PhotoData:
        """Build the PhotoData wrapper for downstream processing."""
        metadata = CameraMetadata(
            camera_name=self.camera.name,
            camera_settings=self.settings.model,
            raw_metadata={"driver": "linuxpy"},
        )
        return PhotoData(data=data, format=data_format, camera_metadata=metadata)

    def _get_preview_resolution(self) -> Tuple[int, int]:
        """Return the preview resolution, falling back to defaults."""
        return self.settings.preview_resolution or self._DEFAULT_PREVIEW_RESOLUTION

    def _get_photo_resolution(self) -> Tuple[int, int]:
        """Return the photo resolution, falling back to defaults."""
        return self.settings.photo_resolution or self._DEFAULT_PHOTO_RESOLUTION

