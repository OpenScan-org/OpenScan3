import pytest
import asyncio  # Still needed for async functions
from unittest.mock import AsyncMock, MagicMock  # Keep for specific mock types

# Adjust paths if necessary for your project structure
from app.controllers.hardware.motors import MotorController
from app.models.motor import Motor
from app.config.motor import MotorConfig

# The module paths for patching are relative to where they are *used*
# (i.e., in 'app.controllers.hardware.motors.py')
GPIO_PATCH_PATH = 'app.controllers.hardware.motors.gpio'
TIME_PATCH_PATH = 'app.controllers.hardware.motors.time'
ASYNCIO_PATCH_PATH = 'app.controllers.hardware.motors.asyncio'
MATH_PATCH_PATH = 'app.controllers.hardware.motors.math'  # math.cos is used in _execute_movement

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
def motor_config_instance():
    """Provides a MotorConfig instance for tests."""
    return MotorConfig(
        direction_pin=1, enable_pin=2, step_pin=3,
        acceleration=20000, max_speed=7500,
        min_angle=0, max_angle=360,
        direction=1, steps_per_rotation=3200
    )


@pytest.fixture
def motor_model_instance(motor_config_instance):
    """Provides a Motor model instance, initialized at angle 0."""
    return Motor(name="test_motor", settings=motor_config_instance, angle=90.0)


@pytest.fixture
def mocked_dependencies(mocker, event_loop):  # Added event_loop here
    """Mocks GPIO, time.sleep, math.cos, and event_loop.run_in_executor."""
    mock_gpio = mocker.patch(GPIO_PATCH_PATH)
    mock_time_sleep = mocker.patch(TIME_PATCH_PATH + '.sleep')
    mock_math_cos = mocker.patch(MATH_PATCH_PATH + '.cos', return_value=0.0)

    # This side effect will be called when event_loop.run_in_executor is called.
    # It should execute the callback synchronously.
    def sync_run_in_executor_side_effect(executor, callback, *args):
        # 'executor' is the first argument passed to run_in_executor (e.g., self._executor from MotorController)
        # 'callback' is the function to run (e.g., do_movement)
        # '*args' are arguments for the callback
        return callback(*args)  # Directly call the 'do_movement' function

    # Patch run_in_executor on the specific event_loop instance used by the test
    mock_run_in_executor = mocker.patch.object(
        event_loop,
        'run_in_executor',
        new_callable=AsyncMock,  # Make it an AsyncMock
        side_effect=sync_run_in_executor_side_effect
    )

    return {
        "gpio": mock_gpio,
        "time_sleep": mock_time_sleep,
        "math_cos": mock_math_cos,
        "run_in_executor": mock_run_in_executor  # Mock for event_loop.run_in_executor
    }


@pytest.fixture
def motor_controller_instance(motor_model_instance, motor_config_instance, mocked_dependencies, mocker):
    """Provides a MotorController instance with mocked dependencies."""
    # Ensure motor_model_instance is correctly passed if found
    controller = MotorController(motor=motor_model_instance) # Corrected argument name
    controller.is_busy = MagicMock(return_value=False)
    controller._stop_requested = False
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
def motor_controller_clamping_instance(motor_model_clamping_instance, motor_config_clamping_instance, mocked_dependencies, mocker):
    """Provides a MotorController instance with mocked dependencies."""
    # Ensure motor_model_instance is correctly passed if found
    controller = MotorController(motor=motor_model_clamping_instance) # Corrected argument name
    controller.is_busy = MagicMock(return_value=False)
    controller._stop_requested = False
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

