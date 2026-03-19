"""Background task for scanning QR codes via the camera.

This task continuously captures preview frames, runs QR code detection, and
when a WiFi QR code is recognized, applies the credentials via NetworkManager.

The task runs indefinitely until a WiFi QR code is found or the task is
cancelled.  This makes the setup experience frictionless – the user can take
their time holding the QR code in front of the camera.
"""

from __future__ import annotations

import asyncio
import io
import logging
import time
from typing import AsyncGenerator

import numpy as np

from PIL import Image

from openscan_firmware.controllers.services.tasks.base_task import BaseTask
from openscan_firmware.controllers.services.tasks.task_manager import get_task_manager
from openscan_firmware.models.task import TaskProgress, TaskStatus

logger = logging.getLogger(__name__)

# How often to grab a frame (seconds)
_SCAN_INTERVAL = 0.5
# Give the camera a short warm-up before we start scanning
_STARTUP_DELAY = 3.0
# Downscale preview frames to keep zxing fast but detailed
_MAX_PREVIEW_EDGE = 1400
# Throttle how often we emit "no QR yet" info messages to keep logs calm
_NO_HIT_INFO_INTERVAL = 30
# Emit TaskProgress updates at most every N seconds while idle scanning
_PROGRESS_UPDATE_INTERVAL = 30.0
# Cooldown between connection attempts for the same SSID (seconds)
_CONNECT_RETRY_COOLDOWN = 6.0
# Sleep after a failed connection attempt before resuming scanning
_CONNECT_ERROR_BACKOFF = 2.0


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
        import numpy as np
        from openscan_firmware.controllers.hardware.cameras.camera import get_camera_controller
        from openscan_firmware.utils.wifi import parse_wifi_qr, connect_wifi
        from openscan_firmware.utils.qr_reader import ZxingQRReader, StableQRConsensus

        yield TaskProgress(current=0, total=0, message="QR scan starting – warming up the camera")

        if _STARTUP_DELAY > 0:
            await asyncio.sleep(_STARTUP_DELAY)

        await _cleanup_stale_qr_tasks()

        controller = get_camera_controller(camera_name)
        reader = ZxingQRReader()
        # Two confirmations within the last five frames are enough; this keeps the
        # scan responsive even when individual preview frames fail.
        consensus = StableQRConsensus(reader, required_hits=2, window=5)

        yield TaskProgress(current=0, total=0, message="QR scan ready – hold a WiFi QR code in front of the camera")

        attempt = 0
        last_progress_emit = 0.0
        last_credentials = None
        last_connect_attempt = 0.0
        while True:
            attempt += 1

            await self.wait_for_pause()
            if self.is_cancelled():
                logger.info("QR scan task cancelled at attempt %d", attempt)
                return

            # Capture a preview frame (JPEG) and convert it to an RGB numpy array
            try:
                frame_for_decode = await _capture_preview_array(controller)
                if frame_for_decode is None:
                    logger.debug("QR scan attempt %d: preview frame unavailable", attempt)
                    yield TaskProgress(current=attempt, total=0, message="Waiting for preview frame...")
                    await asyncio.sleep(_SCAN_INTERVAL)
                    continue

            except Exception as exc:
                logger.warning("Preview capture failed on attempt %d: %s", attempt, exc)
                yield TaskProgress(current=attempt, total=0, message=f"Preview error: {exc}")
                await asyncio.sleep(_SCAN_INTERVAL)
                continue

            # Detect QR codes in the frame using the robust reader with consensus
            decoded_text = consensus.feed(frame_for_decode)

            if decoded_text and decoded_text.startswith("WIFI:"):
                try:
                    credentials = parse_wifi_qr(decoded_text)
                except ValueError as exc:
                    logger.warning("Ignoring invalid WiFi QR code: %s", exc)
                    continue

                now = time.monotonic()
                same_credentials = last_credentials is not None and credentials == last_credentials
                within_cooldown = same_credentials and (now - last_connect_attempt) < _CONNECT_RETRY_COOLDOWN

                if within_cooldown:
                    logger.debug(
                        "Skipping connection attempt for SSID '%s' due to cooldown.",
                        credentials.ssid,
                    )
                    continue

                last_credentials = credentials
                last_connect_attempt = now

                logger.info("WiFi QR code detected for SSID '%s'", credentials.ssid)
                yield TaskProgress(current=attempt, total=0, message="WiFi QR code detected! Connecting...")

                try:
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

                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    error_msg = f"Failed to apply WiFi credentials: {exc}"
                    logger.error(error_msg)
                    self._task_model.result = {"error": error_msg}
                    yield TaskProgress(
                        current=attempt,
                        total=0,
                        message=f"{error_msg} – retrying shortly",
                    )
                    await asyncio.sleep(_CONNECT_ERROR_BACKOFF)
                    continue

            elif decoded_text:
                logger.debug("Non-WiFi QR code found: %s", decoded_text[:50])

            now = time.monotonic()
            if last_progress_emit == 0.0 or (now - last_progress_emit) >= _PROGRESS_UPDATE_INTERVAL:
                yield TaskProgress(current=attempt, total=0, message=f"Scanning... (attempt {attempt})")
                last_progress_emit = now
            await asyncio.sleep(_SCAN_INTERVAL)


async def _capture_preview_array(controller) -> "np.ndarray | None":
    """Fetch a preview frame from the controller and return it as an RGB numpy array."""
    preview_io = await controller.preview_async()
    if preview_io is None:
        return None

    if isinstance(preview_io, bytes):
        data = preview_io
        preview_io = None
    else:
        try:
            data = preview_io.read()
        finally:
            try:
                preview_io.close()
            except Exception:  # noqa: BLE001
                pass

    try:
        with Image.open(io.BytesIO(data)) as img:
            img = img.convert("RGB")
            img = _downscale_image(img, _MAX_PREVIEW_EDGE)
            frame = np.array(img)
    except Exception as exc:
        logger.debug("Failed to decode preview JPEG: %s", exc)
        return None

    return frame


def _downscale_image(image: Image.Image, max_edge: int) -> Image.Image:
    if max_edge <= 0:
        return image

    width, height = image.size
    current_edge = max(width, height)
    if current_edge <= max_edge:
        return image

    scale = max_edge / float(current_edge)
    new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
    return image.resize(new_size, Image.LANCZOS)


async def _cleanup_stale_qr_tasks() -> None:
    """Remove cancelled/interrupted QR tasks and trim error history to last three."""
    task_manager = get_task_manager()
    relevant = [task for task in task_manager.get_all_tasks_info() if task.task_type == QrScanTask.task_name]

    stale_statuses = {TaskStatus.CANCELLED, TaskStatus.INTERRUPTED}
    removed = 0

    for task in relevant:
        if task.status not in stale_statuses:
            continue
        try:
            await task_manager.delete_task(task.id)
            removed += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to delete stale QR task %s: %s", task.id, exc)

    error_tasks = sorted(
        (task for task in relevant if task.status == TaskStatus.ERROR),
        key=lambda task: task.created_at,
        reverse=True,
    )
    for task in error_tasks[3:]:
        try:
            await task_manager.delete_task(task.id)
            removed += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to delete old QR task error %s: %s", task.id, exc)

    if removed:
        logger.debug("Cleaned up %d stale QR WiFi scan tasks", removed)
