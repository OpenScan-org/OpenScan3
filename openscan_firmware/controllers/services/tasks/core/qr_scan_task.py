"""Background task for scanning QR codes via the camera.

This task continuously captures preview frames, runs QR code detection, and
when a WiFi QR code is recognized, applies the credentials via NetworkManager.

The task runs indefinitely until a WiFi QR code is found or the task is
cancelled.  This makes the setup experience frictionless – the user can take
their time holding the QR code in front of the camera.
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncGenerator

from openscan_firmware.controllers.services.tasks.base_task import BaseTask
from openscan_firmware.models.task import TaskProgress

logger = logging.getLogger(__name__)

# How often to grab a frame (seconds)
_SCAN_INTERVAL = 0.5


class QrScanTask(BaseTask):
    """Scan camera preview frames for WiFi QR codes and apply credentials.

    This is a non-exclusive async task that runs indefinitely.  While it runs,
    the preview stream remains usable because the existing ``_hw_lock`` in
    ``CameraController`` handles concurrent access gracefully (preview returns
    ``None`` while a capture is in progress and vice-versa).

    The task terminates when:
    - A WiFi QR code is successfully detected and the connection is established.
    - The task is cancelled (e.g. by the user or another service).
    """

    task_name = "qr_scan_task"
    task_category = "core"
    is_exclusive = False
    is_blocking = False

    async def run(
        self,
        camera_name: str,
    ) -> AsyncGenerator[TaskProgress, None]:
        """Capture frames and look for WiFi QR codes.

        Runs indefinitely until a WiFi QR code is found or the task is
        cancelled.  Progress ``total`` is set to 0 to signal an indeterminate
        task to the frontend.

        Args:
            camera_name: Name of the camera controller to use for captures.

        Yields:
            TaskProgress updates for the frontend.
        """
        # Lazy imports to avoid side effects at module load time
        import cv2
        from openscan_firmware.controllers.hardware.cameras.camera import get_camera_controller
        from openscan_firmware.utils.wifi import parse_wifi_qr, connect_wifi

        controller = get_camera_controller(camera_name)
        detector = cv2.QRCodeDetector()

        yield TaskProgress(current=0, total=0, message="QR scan started – hold a WiFi QR code in front of the camera")

        attempt = 0
        while True:
            attempt += 1

            await self.wait_for_pause()
            if self.is_cancelled():
                logger.info("QR scan task cancelled at attempt %d", attempt)
                return

            # Capture a full-resolution frame via the async path.
            # photo_async("rgb_array") gives us a numpy array suitable for cv2.
            try:
                photo_data = await controller.photo_async("rgb_array")
                frame = photo_data.data
            except Exception as exc:
                logger.warning("Frame capture failed on attempt %d: %s", attempt, exc)
                yield TaskProgress(current=attempt, total=0, message=f"Capture error: {exc}")
                await asyncio.sleep(_SCAN_INTERVAL)
                continue

            # Detect QR codes in the frame
            decoded_text, points, _ = detector.detectAndDecode(frame)

            if decoded_text and decoded_text.startswith("WIFI:"):
                logger.info("WiFi QR code detected: %s", decoded_text[:30] + "...")
                yield TaskProgress(current=attempt, total=0, message="WiFi QR code detected! Connecting...")

                try:
                    credentials = parse_wifi_qr(decoded_text)
                    output = await asyncio.to_thread(connect_wifi, credentials)
                    result_msg = f"Connected to '{credentials.ssid}'"
                    logger.info(result_msg)

                    self._task_model.result = {
                        "ssid": credentials.ssid,
                        "security": credentials.security,
                        "hidden": credentials.hidden,
                        "nmcli_output": output,
                    }

                    yield TaskProgress(current=1, total=1, message=result_msg)
                    return

                except Exception as exc:
                    error_msg = f"Failed to apply WiFi credentials: {exc}"
                    logger.error(error_msg)
                    self._task_model.result = {"error": error_msg}
                    raise RuntimeError(error_msg) from exc

            elif decoded_text:
                logger.debug("Non-WiFi QR code found: %s", decoded_text[:50])

            yield TaskProgress(current=attempt, total=0, message=f"Scanning... (attempt {attempt})")
            await asyncio.sleep(_SCAN_INTERVAL)
