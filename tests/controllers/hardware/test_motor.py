import pytest
import asyncio  # Still needed for async functions
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock  # Keep for specific mock types

# Adjust paths if necessary for your project structure
from openscan_firmware.controllers.hardware.motors import MotorController
from openscan_firmware.models.motor import Motor
from openscan_firmware.config.motor import MotorConfig

# The module paths for patching are relative to where they are *used*
# (i.e., in 'openscan_firmware.controllers.hardware.motors')
GPIO_PATCH_PATH = 'openscan_firmware.controllers.hardware.motors.gpio'
TIME_PATCH_PATH = 'openscan_firmware.controllers.hardware.motors.time'
ASYNCIO_PATCH_PATH = 'openscan_firmware.controllers.hardware.motors.asyncio'
MATH_PATCH_PATH = 'openscan_firmware.controllers.hardware.motors.math'  # math.cos is used in _execute_movement

# --- Test Data ---
MOVE_DEGREES_CASES = [
    # (initial_angle, move_degrees_val, expected_final_angle)
    (0, 90, 90.0),
    (0, -90, 270.0),
    (45, 30, 75.0),
    (45, -30, 15.0),
    (350, 20, 10.0),
    (10, -20, 350.0),
    (0, 360, 0.0),
    (0, -360, 0.0),
    (0, 720, 0.0),
    (0, 0, 0.0),
]

MOVE_TO_CASES = [
    # (initial_angle, target_degrees_val, expected_final_angle)
    (0, 90, 90.0),
    (0, -90, 270.0),
    (45, 10, 10.0),
    (170, -10, 350.0),
    (0, 0, 0.0),
    (180, 180, 180.0),
]





@pytest.fixture
def motor_event_loop():
    """Provides a dedicated event loop for motor controller tests."""
    loop = asyncio.new_event_loop()
    try:
        yield loop
    finally:
        loop.close()


@pytest.fixture
def mocked_dependencies(monkeypatch, motor_event_loop):
    """Mocks GPIO, time.sleep, math.cos, and the low-level movement executor."""

    import openscan_firmware.controllers.hardware.motors as motors_module

    mock_gpio = MagicMock()
    monkeypatch.setattr(motors_module, 'gpio', mock_gpio)

    mock_time_sleep = MagicMock()
    monkeypatch.setattr(motors_module.time, 'sleep', mock_time_sleep)

    mock_math_cos = MagicMock(return_value=0.0)
    monkeypatch.setattr(motors_module.math, 'cos', mock_math_cos)
    async def fake_execute_movement(self, step_count: int, requested_degrees: float) -> int:
        self.model.angle = requested_degrees % 360
        return abs(step_count)

    monkeypatch.setattr(MotorController, '_execute_movement', fake_execute_movement)
    mock_run_in_executor = AsyncMock()

    return {
        "gpio": mock_gpio,
        "time_sleep": mock_time_sleep,
        "math_cos": mock_math_cos,
        "run_in_executor": mock_run_in_executor,
        "event_loop": motor_event_loop,
    }


@pytest.fixture
def movement_dependencies(monkeypatch):
    """Mocks hardware and executor boundaries while keeping _execute_movement real."""
    import openscan_firmware.controllers.hardware.motors as motors_module

    mock_gpio = MagicMock()
    monkeypatch.setattr(motors_module, 'gpio', mock_gpio)
    monkeypatch.setattr(motors_module.time, 'sleep', MagicMock())
    monkeypatch.setattr(motors_module, 'notify_busy_change', MagicMock())

    class ImmediateExecutorLoop:
        def run_in_executor(self, executor, callback, *args):
            future = asyncio.Future()
            try:
                future.set_result(callback(*args))
            except Exception as exc:
                future.set_exception(exc)
            return future

    monkeypatch.setattr(
        motors_module,
        'asyncio',
        SimpleNamespace(
            CancelledError=asyncio.CancelledError,
            get_event_loop=MagicMock(return_value=ImmediateExecutorLoop()),
        ),
    )

    return {
        "gpio": mock_gpio,
        "notify_busy_change": motors_module.notify_busy_change,
    }


@pytest.fixture
def motor_controller_instance(motor_model_instance, motor_config_instance, mocked_dependencies):
    """Provides a MotorController instance with mocked dependencies."""
    # Ensure motor_model_instance is correctly passed if found
    controller = MotorController(motor=motor_model_instance) # Corrected argument name
    controller.is_busy = MagicMock(return_value=False)
    controller._stop_requested = False
    controller.set_idle_callbacks(lambda: False, AsyncMock())
    # If MotorController explicitly creates/uses an executor, e.g. self._executor,
    # you might need to mock it if it's not implicitly handled by mocking run_in_executor's loop.
    # controller._executor = MagicMock() # Example if it has its own executor instance
    return controller


# --- Test Functions ---

@pytest.mark.asyncio
@pytest.mark.parametrize("initial_angle, move_val, expected_angle", MOVE_DEGREES_CASES)
async def test_move_degrees(motor_controller_instance, motor_model_instance, mocked_dependencies,
                            initial_angle, move_val, expected_angle):
    """Tests move_degrees method: motor.angle should be updated correctly."""
    controller = motor_controller_instance
    motor_model = motor_model_instance

    motor_model.angle = float(initial_angle)
    controller._stop_requested = False

    mocked_dependencies["gpio"].reset_mock()
    mocked_dependencies["time_sleep"].reset_mock()
    mocked_dependencies["math_cos"].reset_mock()
    mocked_dependencies["run_in_executor"].reset_mock()


    await controller.move_degrees(float(move_val))

    assert motor_model.angle == pytest.approx(expected_angle, abs=1), \
        f"Angle mismatch for move_degrees({move_val}) from {initial_angle}"


@pytest.mark.asyncio
@pytest.mark.parametrize("initial_angle, target_val, expected_angle", MOVE_TO_CASES)
async def test_move_to(motor_controller_instance, motor_model_instance, mocked_dependencies,
                       initial_angle, target_val, expected_angle):
    """Tests move_to method: motor.angle should be set to the target angle (% 360)."""
    controller = motor_controller_instance
    motor_model = motor_model_instance

    motor_model.angle = float(initial_angle)
    controller._stop_requested = False

    mocked_dependencies["gpio"].reset_mock()
    mocked_dependencies["time_sleep"].reset_mock()
    mocked_dependencies["math_cos"].reset_mock()
    mocked_dependencies["run_in_executor"].reset_mock()

    await controller.move_to(float(target_val))

    # Treat -180 and 180 as equivalent
    if expected_angle == 180.0:
        assert motor_model.angle == pytest.approx(180.0, abs=1) or motor_model.angle == pytest.approx(-180.0, abs=1), \
            f"Angle mismatch for move_to({target_val}) from {initial_angle} (expected ±180°)"
    else:
        assert motor_model.angle == pytest.approx(expected_angle, abs=1), \
            f"Angle mismatch for move_to({target_val}) from {initial_angle}"


# --- Test Clamping ---

@pytest.fixture
def motor_config_clamping_instance():
    """Provides a MotorConfig instance for tests."""
    return MotorConfig(
        direction_pin=1, enable_pin=2, step_pin=3,
        acceleration=20000, max_speed=7500,
        min_angle=0, max_angle=150,
        direction=1, steps_per_rotation=3200
    )


@pytest.fixture
def motor_model_clamping_instance(motor_config_clamping_instance):
    """Provides a Motor model instance, initialized at angle 0."""
    return Motor(name="test_motor", settings=motor_config_clamping_instance, angle=90.0)

@pytest.fixture
def motor_controller_clamping_instance(motor_model_clamping_instance, motor_config_clamping_instance, mocked_dependencies):
    """Provides a MotorController instance with mocked dependencies."""
    # Ensure motor_model_instance is correctly passed if found
    controller = MotorController(motor=motor_model_clamping_instance) # Corrected argument name
    controller.is_busy = MagicMock(return_value=False)
    controller._stop_requested = False
    controller.set_idle_callbacks(lambda: False, AsyncMock())
    # If MotorController explicitly creates/uses an executor, e.g. self._executor,
    # you might need to mock it if it's not implicitly handled by mocking run_in_executor's loop.
    # controller._executor = MagicMock() # Example if it has its own executor instance
    return controller

MOVE_DEGREES_CASES = [
    # (initial_angle, move_degrees_val, expected_final_angle)
    (0, 90, 90.0),
    (0, -90, 0.0),
    (100, 90, 150.0),
    (160, -20, 140.0),
]

MOVE_TO_CASES = [
    # (initial_angle, target_degrees_val, expected_final_angle)
    (0, 90, 90.0),
    (0, 200, 150.0),
    (90, -20, 150.0), # because normalization happens before clamping...
    (0, -20, 150.0), # ... so it normalizes -20 to 340 and clamps to 150 in both cases
]

@pytest.mark.asyncio
@pytest.mark.parametrize("initial_angle, move_val, expected_angle", MOVE_DEGREES_CASES)
async def test_move_degrees_with_clamping(motor_controller_clamping_instance, motor_model_clamping_instance, mocked_dependencies,
                            initial_angle, move_val, expected_angle):
    """Tests move_degrees method: motor.angle should be updated correctly."""
    controller = motor_controller_clamping_instance
    motor_model = motor_model_clamping_instance

    motor_model.angle = float(initial_angle)
    controller._stop_requested = False

    mocked_dependencies["gpio"].reset_mock()
    mocked_dependencies["time_sleep"].reset_mock()
    mocked_dependencies["math_cos"].reset_mock()
    mocked_dependencies["run_in_executor"].reset_mock()


    await controller.move_degrees(float(move_val))

    assert motor_model.angle == pytest.approx(expected_angle, abs=1), \
        f"Angle mismatch for move_degrees({move_val}) from {initial_angle}"


@pytest.mark.asyncio
@pytest.mark.parametrize("initial_angle, target_val, expected_angle", MOVE_TO_CASES)
async def test_move_to_with_clamping(motor_controller_clamping_instance, motor_model_clamping_instance, mocked_dependencies,
                       initial_angle, target_val, expected_angle):
    """Tests move_to method: motor.angle should be set to the target angle (% 360)."""
    controller = motor_controller_clamping_instance
    motor_model = motor_model_clamping_instance

    motor_model.angle = float(initial_angle)
    controller._stop_requested = False

    mocked_dependencies["gpio"].reset_mock()
    mocked_dependencies["time_sleep"].reset_mock()
    mocked_dependencies["math_cos"].reset_mock()
    mocked_dependencies["run_in_executor"].reset_mock()

    await controller.move_to(float(target_val))

    # Treat -180 and 180 as equivalent
    if expected_angle == 180.0:
        assert motor_model.angle == pytest.approx(180.0, abs=1) or motor_model.angle == pytest.approx(-180.0, abs=1), \
            f"Angle mismatch for move_to({target_val}) from {initial_angle} (expected ±180°)"
    else:
        assert motor_model.angle == pytest.approx(expected_angle, abs=1), \
            f"Angle mismatch for move_to({target_val}) from {initial_angle}"


@pytest.fixture
def movement_motor_controller(movement_dependencies):
    settings = MotorConfig(
        direction_pin=1,
        enable_pin=2,
        step_pin=3,
        acceleration=20000,
        max_speed=7500,
        min_angle=0,
        max_angle=360,
        direction=1,
        steps_per_rotation=3200,
    )
    controller = MotorController(Motor(name="test_motor", settings=settings, angle=0.0))
    controller.set_idle_callbacks(lambda: False, AsyncMock())
    controller._pre_calculate_step_times = MagicMock(return_value=[0.0, 0.0, 0.0])
    return controller


@pytest.mark.asyncio
async def test_execute_movement_sets_direction_and_steps_forward(movement_motor_controller, movement_dependencies):
    controller = movement_motor_controller

    await controller._execute_movement(3, 0.0)

    assert controller.model.angle == pytest.approx(3 / 3200 * 360)
    movement_dependencies["gpio"].set_output_pin.assert_any_call(controller.settings.direction_pin, True)
    assert movement_dependencies["gpio"].set_output_pin.call_args_list.count(
        ((controller.settings.step_pin, True),)
    ) == 3
    assert movement_dependencies["gpio"].set_output_pin.call_args_list.count(
        ((controller.settings.step_pin, False),)
    ) == 3
    assert controller._current_steps == 0


@pytest.mark.asyncio
async def test_execute_movement_sets_direction_and_updates_angle_backward(
    movement_motor_controller,
    movement_dependencies,
):
    controller = movement_motor_controller
    controller.model.angle = 10.0

    await controller._execute_movement(-3, 0.0)

    assert controller.model.angle == pytest.approx((10.0 - (3 / 3200 * 360)) % 360)
    movement_dependencies["gpio"].set_output_pin.assert_any_call(controller.settings.direction_pin, False)


@pytest.mark.asyncio
async def test_execute_movement_stops_when_stop_requested(
    movement_motor_controller,
    movement_dependencies,
):
    controller = movement_motor_controller
    controller._pre_calculate_step_times = MagicMock(return_value=[0.0, 1.0, 2.0, 3.0])

    step_high_calls = 0

    def set_output_pin(pin, value):
        nonlocal step_high_calls
        if pin == controller.settings.step_pin and value is True:
            step_high_calls += 1
            controller._stop_requested = True

    movement_dependencies["gpio"].set_output_pin.side_effect = set_output_pin

    await controller._execute_movement(4, 0.0)

    assert step_high_calls == 1
    assert controller.model.angle == pytest.approx(1 / 3200 * 360)
    assert controller._current_steps == 0
