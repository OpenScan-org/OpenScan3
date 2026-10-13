"""Opt-in real-hardware exposure scan over one complete turntable rotation.

Run on the Raspberry Pi with the service stopped, for example:

    OPENSCAN_RUN_REAL_CAMERA_TESTS=1 \
    .venv/bin/python -m pytest -s \
    tests/controllers/hardware/picamera2/test_exposure_turntable_real.py

The test controls the turntable and camera in one process. It prints one
``TURN_EXPOSURE_SCAN`` line per measurement and intentionally does not assert
specific shutter values because the measured exposure depends on the scene.
It does assert that every calibration returns before the exposure timeout.
"""

import asyncio
import os
import time

import pytest

from openscan_firmware.config.camera import CameraSettings
from openscan_firmware.config.motor import MotorConfig
from openscan_firmware.controllers.hardware import gpio
from openscan_firmware.controllers.hardware.cameras.camera import (
    create_camera_controller,
    is_camera_type_available,
    remove_camera_controller,
)
from openscan_firmware.controllers.hardware.motors import (
    create_motor_controller,
    remove_motor_controller,
)
from openscan_firmware.models.camera import Camera, CameraType
from openscan_firmware.models.motor import Motor


def _env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _env_float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


@pytest.fixture
def real_ringlight():
    if not _env_flag("OPENSCAN_RUN_REAL_CAMERA_TESTS", False):
        pytest.skip("Set OPENSCAN_RUN_REAL_CAMERA_TESTS=1 to access real camera hardware.")

    pins = [int(item.strip()) for item in os.getenv("OPENSCAN_RINGLIGHT_PINS", "17,27").split(",")]
    frequency = _env_int("OPENSCAN_RINGLIGHT_PWM_FREQUENCY", 10000)
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
def real_turntable(real_ringlight):
    if not is_camera_type_available(CameraType.PICAMERA2):
        pytest.skip("Picamera2 dependencies are not available.")

    start_angle = _env_float("OPENSCAN_TURNTABLE_START_ANGLE", 90.0)
    motor_name = "turntable"
    motor = Motor(
        name=motor_name,
        angle=start_angle,
        settings=MotorConfig(
            direction_pin=_env_int("OPENSCAN_TURNTABLE_DIRECTION_PIN", 9),
            enable_pin=_env_int("OPENSCAN_TURNTABLE_ENABLE_PIN", 22),
            step_pin=_env_int("OPENSCAN_TURNTABLE_STEP_PIN", 11),
            acceleration=_env_int("OPENSCAN_TURNTABLE_ACCELERATION", 5000),
            max_speed=_env_int("OPENSCAN_TURNTABLE_MAX_SPEED", 5000),
            direction=_env_int("OPENSCAN_TURNTABLE_DIRECTION", 1),
            steps_per_rotation=_env_int("OPENSCAN_TURNTABLE_STEPS_PER_ROTATION", 3200),
            min_angle=0.0,
            max_angle=360.0,
            home_angle=90.0,
        ),
    )
    controller = create_motor_controller(motor)

    async def wake_motor(_event):
        # The application normally supplies this callback through the device
        # controller. Keep the standalone hardware test self-contained while
        # still enabling the driver before the first step pulse.
        gpio.set_output_pin(controller.settings.enable_pin, False)

    controller.set_idle_callbacks(lambda: True, wake_motor)
    try:
        yield controller, start_angle
    finally:
        try:
            asyncio.run(controller.move_to(start_angle))
        finally:
            remove_motor_controller(motor_name)


def _move_to(turntable, target_angle: float) -> float:
    asyncio.run(turntable.move_to(target_angle))
    settle_s = _env_float("OPENSCAN_TURNTABLE_SETTLE_S", 1.0)
    time.sleep(settle_s)
    return float(turntable.model.angle)


@pytest.mark.parametrize(
    ("crop_width", "crop_height", "mode"),
    [
        # crop_width/crop_height describe the percentage removed from the
        # centered image. Keep 80% of the width and 50% of the height.
        pytest.param(20, 50, "photo-cropped", id="photo-cropped"),
        pytest.param(0, 0, "photo-uncropped", id="photo-uncropped"),
    ],
)
def test_real_exposure_over_full_turntable_rotation(
    real_turntable,
    crop_width,
    crop_height,
    mode,
):
    turntable, start_angle = real_turntable
    if not is_camera_type_available(CameraType.PICAMERA2):
        pytest.skip("Picamera2 dependencies are not available.")

    camera_name = os.getenv("OPENSCAN_CAMERA_NAME", "imx519")
    camera_path = os.getenv("OPENSCAN_CAMERA_PATH", "/dev/video0")
    orientation_flag = int(os.getenv("OPENSCAN_CAMERA_ORIENTATION", "6"))
    timeout_s = _env_float("OPENSCAN_EXPOSURE_TIMEOUT_S", 4.0)
    camera = Camera(
        type=CameraType.PICAMERA2,
        name=camera_name,
        path=camera_path,
        settings=CameraSettings(
            crop_width=crop_width,
            crop_height=crop_height,
            orientation_flag=orientation_flag,
            shutter=50.0,
            gain=1.0,
            awbg_red=1.57,
            awbg_blue=2.27,
        ),
    )

    controller = create_camera_controller(camera)
    try:
        for point_index in range(10):
            target_angle = (start_angle + point_index * 36.0) % 360.0
            actual_angle = _move_to(turntable, target_angle)
            started = time.monotonic()
            try:
                shutter_ms, analogue_gain = controller.calibrate_exposure_and_lock(
                    warmup_frames=12,
                    stable_frames=4,
                    timeout_s=timeout_s,
                    use_photo_config=True,
                )
            except RuntimeError as exc:
                elapsed_s = time.monotonic() - started
                timed_out = str(exc) == "Exposure did not stabilize."
                print(
                    "TURN_EXPOSURE_SCAN "
                    f"mode={mode} point={point_index + 1}/10 "
                    f"target_angle={target_angle:.3f} actual_angle={actual_angle:.3f} "
                    f"status=failed elapsed_s={elapsed_s:.3f} "
                    f"timeout_s={timeout_s:.3f} timed_out={timed_out} error={exc}",
                    flush=True,
                )
                pytest.fail(
                    f"{mode} exposure failed at turntable angle {actual_angle:.3f} "
                    f"after {elapsed_s:.3f}s: {exc}"
                )

            elapsed_s = time.monotonic() - started
            print(
                "TURN_EXPOSURE_SCAN "
                f"mode={mode} point={point_index + 1}/10 "
                f"target_angle={target_angle:.3f} actual_angle={actual_angle:.3f} "
                f"status=success elapsed_s={elapsed_s:.3f} "
                f"timeout_s={timeout_s:.3f} settled_before_timeout=true "
                f"shutter_ms={shutter_ms:.3f} analogue_gain={analogue_gain:.3f}",
                flush=True,
            )

            assert shutter_ms > 0
            assert analogue_gain == pytest.approx(1.0)
    finally:
        remove_camera_controller(camera_name)
