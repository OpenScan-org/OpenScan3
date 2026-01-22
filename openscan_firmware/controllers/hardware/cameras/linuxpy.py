import logging
from typing import IO

from linuxpy.video.device import Device  # type: ignore[import]

from openscan_firmware.config.camera import CameraSettings
from openscan_firmware.models.camera import Camera, PhotoData

from .camera import CameraController

logger = logging.getLogger(__name__)


class LINUXPYCamera(CameraController):
    """Boilerplate controller for LinuxPy based USB cameras."""

    def __init__(self, camera: Camera):
        super().__init__(camera)
        self._device: Device | None = None
        logger.info("Initialized LinuxPy controller for camera '%s'.", camera.name)

    def _apply_settings_to_hardware(self, settings: CameraSettings) -> None:
        """Apply camera settings to the LinuxPy backend.

        Args:
            settings (CameraSettings): Camera configuration to be pushed to hardware.
        """
        logger.debug(
            "LinuxPy settings updated for '%s': %s",
            self.camera.name,
            settings.model_dump_json(),
        )

    def preview(self) -> IO[bytes]:
        """Capture a low resolution preview frame."""
        raise NotImplementedError("LinuxPy preview is not implemented yet.")

    def capture_rgb_array(self) -> PhotoData:
        """Capture an RGB array for analysis."""
        raise NotImplementedError("LinuxPy RGB capture is not implemented yet.")

    def capture_yuv_array(self) -> PhotoData:
        """Capture a YUV array for analysis."""
        raise NotImplementedError("LinuxPy YUV capture is not implemented yet.")

    def capture_dng(self) -> PhotoData:
        """Capture a RAW/DNG frame."""
        raise NotImplementedError("LinuxPy DNG capture is not implemented yet.")

    def capture_jpeg(self) -> PhotoData:
        """Capture a JPEG frame."""
        raise NotImplementedError("LinuxPy JPEG capture is not implemented yet.")

