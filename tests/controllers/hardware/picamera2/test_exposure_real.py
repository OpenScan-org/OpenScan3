"""Opt-in exposure calibration test for a real Picamera2 camera.

Run explicitly on the Raspberry Pi, for example:

    OPENSCAN_RUN_REAL_CAMERA_TESTS=1 \
    .venv/bin/python -m pytest -s \
    tests/controllers/hardware/picamera2/test_exposure_real.py

The test exercises both the active preview configuration and the cropped photo
configuration. It requires the ringlight to be connected to the default PWM
pins 17 and 27. Override them with ``OPENSCAN_RINGLIGHT_PINS`` when needed.
"""

import os
import time

import pytest

from openscan_firmware.config.camera import CameraSettings
from openscan_firmware.controllers.hardware import gpio
from openscan_firmware.controllers.hardware.cameras.camera import (
    create_camera_controller,
    is_camera_type_available,
    remove_camera_controller,
)
from openscan_firmware.models.camera import Camera, CameraType


def _env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int_list(name: str, default: str) -> list[int]:
    value = os.getenv(name, default)
    return [int(item.strip()) for item in value.split(",") if item.strip()]


@pytest.fixture
def real_ringlight():
    if not _env_flag("OPENSCAN_RUN_REAL_CAMERA_TESTS", False):
        pytest.skip("Set OPENSCAN_RUN_REAL_CAMERA_TESTS=1 to access real camera hardware.")

    pins = _env_int_list("OPENSCAN_RINGLIGHT_PINS", "17,27")
    frequency = int(os.getenv("OPENSCAN_RINGLIGHT_PWM_FREQUENCY", "10000"))
    gpio.initialize_pwm_pins(pins, frequency)
    for pin in pins:
        gpio.set_pwm_pin(pin, 1.0)

    print(
        f"RINGLIGHT_DEBUG pins={','.join(map(str, pins))} duty=100%",
        flush=True,
    )
    try:
        yield
    finally:
        gpio.cleanup_all_pins()


@pytest.fixture
def real_camera_controller(real_ringlight):
    if not _env_flag("OPENSCAN_RUN_REAL_CAMERA_TESTS", False):
        pytest.skip("Set OPENSCAN_RUN_REAL_CAMERA_TESTS=1 to access real camera hardware.")

    if not is_camera_type_available(CameraType.PICAMERA2):
        pytest.skip("Picamera2 dependencies are not available.")

    camera_name = os.getenv("OPENSCAN_CAMERA_NAME", "arducam_64mp")
    camera_path = os.getenv("OPENSCAN_CAMERA_PATH", "/dev/video0")
    orientation_flag = int(os.getenv("OPENSCAN_CAMERA_ORIENTATION", "6"))

    camera = Camera(
        type=CameraType.PICAMERA2,
        name=camera_name,
        path=camera_path,
        settings=CameraSettings(
            crop_width=87,
            crop_height=48,
            orientation_flag=orientation_flag,
            shutter=50.0,
            gain=1.0,
        ),
    )

    controller = create_camera_controller(camera)
    try:
        yield controller
    finally:
        # The camera controller factory caches instances by name. Remove the
        # instance after each parameterized mode so the next case receives a
        # fresh Picamera2 object instead of a cleaned-up controller.
        remove_camera_controller(camera_name)


@pytest.mark.parametrize(
    "use_photo_config",
    [
        pytest.param(False, id="preview"),
        pytest.param(True, id="photo-cropped"),
    ],
)
def test_real_exposure_calibration_completes_before_timeout(
    real_camera_controller,
    use_photo_config,
):
    """Verify both real capture modes converge before the AE timeout.

    The shutter value is intentionally not asserted: it depends on the
    physical scene and illumination. A successful return proves that the
    stabilization loop completed before ``timeout_s``; the timeout path raises
    ``RuntimeError('Exposure did not stabilize.')``.
    """
    timeout_s = float(os.getenv("OPENSCAN_EXPOSURE_TIMEOUT_S", "4.0"))
    mode = "photo-cropped" if use_photo_config else "preview"

    started = time.monotonic()
    try:
        shutter_ms, analogue_gain = real_camera_controller.calibrate_exposure_and_lock(
            warmup_frames=12,
            stable_frames=4,
            timeout_s=timeout_s,
            use_photo_config=use_photo_config,
        )
    except RuntimeError as exc:
        elapsed_s = time.monotonic() - started
        print(
            "EXPOSURE_DEBUG "
            f"status=failed elapsed_s={elapsed_s:.3f} "
            f"timeout_s={timeout_s:.3f} "
            f"mode={mode} "
            f"photo_config={use_photo_config} "
            f"crop_width=87 crop_height=48 "
            f"timed_out={str(exc) == 'Exposure did not stabilize.'} "
            f"error={exc}"
        )
        pytest.fail(f"Exposure calibration failed after {elapsed_s:.3f}s: {exc}")

    elapsed_s = time.monotonic() - started
    print(
        "EXPOSURE_DEBUG "
        f"status=success elapsed_s={elapsed_s:.3f} "
        f"timeout_s={timeout_s:.3f} "
        f"mode={mode} settled_before_timeout=true "
        f"photo_config={use_photo_config} "
        f"crop_width=87 crop_height=48 "
        f"shutter_ms={shutter_ms:.3f} analogue_gain={analogue_gain:.3f}"
    )

    assert shutter_ms > 0
    assert analogue_gain == pytest.approx(1.0)
