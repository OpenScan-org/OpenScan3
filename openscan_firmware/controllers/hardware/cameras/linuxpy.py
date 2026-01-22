"""
LinuxPy camera controller.

This controller is a best-effort example/fallback for generic V4L2 devices
(e.g. USB webcams exposed via ``/dev/video*``).

LinuxPy exposes a lightweight V4L2 wrapper. Unlike the Picamera2 controller we
cannot provide advanced hardware controls (AF/AWB lock, scaler cropping,
RAW/DNG output). To keep the API stable we derive unsupported formats from the
best-effort JPEG stream and warn the user whenever a fallback path is used.

See ``docs/Camera/LINUXPY.md`` for guidance on extending this controller or
creating a dedicated device-specific controller.
"""

import io
import logging
from contextlib import suppress
from typing import IO, Optional, Tuple

import cv2  # type: ignore[import]
import numpy as np
import piexif  # type: ignore[import]
from linuxpy.video.device import Device, VideoCapture  # type: ignore[import]

from openscan_firmware.config.camera import CameraSettings
from openscan_firmware.models.camera import Camera, CameraMetadata, PhotoData

from .camera import CameraController

logger = logging.getLogger(__name__)


class LINUXPYCamera(CameraController):
    """Best-effort V4L2 controller based on LinuxPy."""

    _JPEG_PIXEL_FORMAT = "MJPG"
    _DEFAULT_PREVIEW_RESOLUTION: Tuple[int, int] = (640, 480)
    _DEFAULT_PHOTO_RESOLUTION: Tuple[int, int] = (1920, 1080)
    # Mapping between OpenScan CameraSettings fields and V4L2 control names.
    # Extend this map if your device exposes additional knobs via linuxpy's
    # Device.set_control().
    _CONTROL_MAP = {
        "saturation": "saturation",
        "contrast": "contrast",
        "gain": "gain",
    }
    _JPEG_QUALITY_CONTROL = "jpeg_compression_quality"

    def __init__(self, camera: Camera):
        if camera.settings is None:
            camera.settings = CameraSettings()
        super().__init__(camera)
        self._preview_device: Optional[Device] = None
        self._preview_capture: Optional[VideoCapture] = None
        self._preview_iterator = None
        self._preview_resolution: Optional[Tuple[int, int]] = None
        logger.info("Initialized LinuxPy controller for camera '%s'.", camera.name)

    def _apply_settings_to_hardware(self, settings: CameraSettings) -> None:
        """Persist latest settings (LinuxPy exposes only limited controls)."""
        logger.debug(
            "LinuxPy settings updated for '%s': %s",
            self.camera.name,
            settings.model_dump_json(),
        )
        self._release_preview_capture()
        self._apply_basic_controls(settings)

    def preview(self) -> IO[bytes]:
        """Capture a low-latency preview frame as JPEG bytes."""
        self._set_busy(True)
        try:
            frame_bytes = self._capture_frame(
                self._get_preview_resolution(), reuse_preview_capture=True
            )
            return frame_bytes
        finally:
            self._set_busy(False)

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
        jpeg_bytes = self._embed_orientation_flag(jpeg_bytes)
        jpeg_stream = io.BytesIO(jpeg_bytes)
        return self._create_photo_data(jpeg_stream, "jpeg")

    def cleanup(self):
        """Release preview resources when controller is removed."""
        self._release_preview_capture()

    def _capture_rgb_array(self) -> np.ndarray:
        """Capture a JPEG frame and decode it into an RGB array."""
        jpeg_bytes = self._capture_frame(self._get_photo_resolution())
        np_buffer = np.frombuffer(jpeg_bytes, dtype=np.uint8)
        bgr_image = cv2.imdecode(np_buffer, cv2.IMREAD_COLOR)
        if bgr_image is None:
            raise RuntimeError("LinuxPy failed to decode JPEG stream.")
        return cv2.cvtColor(bgr_image, cv2.COLOR_BGR2RGB)

    def _capture_frame(
        self, resolution: Tuple[int, int], reuse_preview_capture: bool = False
    ) -> bytes:
        """Capture a single JPEG frame with the given resolution."""
        width, height = resolution

        if reuse_preview_capture:
            frame_iter = self._ensure_preview_capture(resolution)
            try:
                frame = next(frame_iter)
            except StopIteration:
                self._preview_iterator = iter(self._preview_capture)  # type: ignore[arg-type]
                next(self._preview_iterator, None)
                frame = next(self._preview_iterator)
            return bytes(frame)

        self._release_preview_capture()
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

    def _ensure_preview_capture(
        self, resolution: Tuple[int, int]
    ):
        """Keep a persistent capture session for preview streaming."""
        if self._preview_capture is None or self._preview_resolution != resolution:
            self._release_preview_capture()
            device = Device(self.camera.path)
            capture = None
            try:
                device.__enter__()
                capture = VideoCapture(device)
                capture.set_format(*resolution, self._JPEG_PIXEL_FORMAT)
                capture.__enter__()
            except Exception:
                if capture is not None:
                    with suppress(Exception):
                        capture.__exit__(None, None, None)
                with suppress(Exception):
                    device.__exit__(None, None, None)
                raise
            self._preview_device = device
            self._preview_capture = capture
            self._preview_iterator = iter(capture)
            next(self._preview_iterator, None)
            self._preview_resolution = resolution
        return self._preview_iterator

    def _release_preview_capture(self):
        """Tear down the cached preview capture session."""
        if self._preview_capture is not None:
            with suppress(Exception):
                self._preview_capture.__exit__(None, None, None)
        if self._preview_device is not None:
            with suppress(Exception):
                self._preview_device.__exit__(None, None, None)
        self._preview_capture = None
        self._preview_device = None
        self._preview_iterator = None
        self._preview_resolution = None

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

    def _apply_basic_controls(self, settings: CameraSettings) -> None:
        """Best-effort mapping of camera settings to generic V4L2 controls."""
        control_device, needs_cleanup = self._acquire_control_device()
        if control_device is None:
            return
        try:
            setter = getattr(control_device, "set_control", None)
            if not callable(setter):
                logger.warning(
                    "Device '%s' does not expose set_control(); skipping control sync.",
                    self.camera.name,
                )
                return

            controls: dict[str, int | float] = {}
            for field, control_name in self._CONTROL_MAP.items():
                value = getattr(settings, field, None)
                if value is not None:
                    controls[control_name] = value

            jpeg_quality = settings.jpeg_quality
            if jpeg_quality is not None:
                controls[self._JPEG_QUALITY_CONTROL] = int(jpeg_quality)

            if not controls:
                return

            for control_name, value in controls.items():
                try:
                    setter(control_name, value)
                    logger.debug(
                        "Applied LinuxPy control %s=%s on '%s'.",
                        control_name,
                        value,
                        self.camera.name,
                    )
                except Exception as exc:
                    logger.warning(
                        "Control '%s' unsupported on '%s': %s",
                        control_name,
                        self.camera.name,
                        exc,
                    )
        finally:
            if needs_cleanup and control_device is not None:
                with suppress(Exception):
                    control_device.__exit__(None, None, None)

    def _acquire_control_device(self) -> tuple[Optional[Device], bool]:
        """Reuse preview device for control updates or open a temporary handle."""
        if self._preview_device is not None:
            return self._preview_device, False
        device = Device(self.camera.path)
        device.__enter__()
        return device, True

    def _embed_orientation_flag(self, jpeg_bytes: bytes) -> bytes:
        """Embed EXIF orientation flag to match Picamera2 behavior."""
        orientation = self.settings.orientation_flag
        if orientation is None:
            return jpeg_bytes
        try:
            flag = int(orientation)
            exif_bytes = piexif.dump(
                {"0th": {piexif.ImageIFD.Orientation: flag}}
            )
            return piexif.insert(exif_bytes, jpeg_bytes)
        except Exception as exc:
            logger.warning(
                "Failed to embed orientation flag (%s) into JPEG: %s",
                orientation,
                exc,
            )
            return jpeg_bytes

