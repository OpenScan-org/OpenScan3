import pytest
from gpiozero.pins.mock import MockFactory
from gpiozero import Device

from openscan_firmware.controllers.hardware.endstops import EndstopController
from openscan_firmware.controllers.hardware.motors import MotorController
from openscan_firmware.config.endstop import EndstopConfig
from openscan_firmware.config.motor import MotorConfig
from openscan_firmware.models.motor import Motor, Endstop
from openscan_firmware.controllers import device as device_controller_module

Device.pin_factory = MockFactory()

@pytest.fixture
def endstop_config():
    return EndstopConfig(pin=4, angular_position=130, motor_name="test_motor", pull_up=True, bounce_time=0.005)

@pytest.fixture
def motor_controller_instance():
    motorconfig = MotorConfig(
        direction_pin=1, enable_pin=2, step_pin=3,
        acceleration=20000, max_speed=50,
        min_angle=0, max_angle=360,
        direction=1, steps_per_rotation=3200
    )
    motor = Motor(name="test_motor", settings=motorconfig)
    return MotorController(motor)

def test_endstop_controller_initialization(endstop_config, motor_controller_instance):
    endstop = Endstop(name="test_endstop", settings=endstop_config)
    controller = EndstopController(endstop, motor_controller_instance)
    assert controller.model.name == "test_endstop"


def test_get_config(endstop_config, motor_controller_instance):
    """Test that get_config returns the correct configuration."""
    endstop = Endstop(name="test_endstop", settings=endstop_config)
    controller = EndstopController(endstop, motor_controller_instance)

    config = controller.get_config()
    assert config.pin == endstop_config.pin
    assert config.angular_position == endstop_config.angular_position
    assert config.motor_name == endstop_config.motor_name


def test_get_status(endstop_config, motor_controller_instance):
    """Test that get_status returns the correct status."""
    endstop = Endstop(name="test_endstop", settings=endstop_config)
    controller = EndstopController(endstop, motor_controller_instance)

    status = controller.get_status()
    assert status["assigned_motor"] == endstop_config.motor_name
    assert status["position"] == endstop_config.angular_position
    assert status["pin"] == endstop_config.pin
    assert isinstance(status["is_pressed"], bool)


def test_apply_settings(endstop_config, motor_controller_instance):
    """Test that _apply_settings updates the configuration correctly."""
    endstop = Endstop(name="test_endstop", settings=endstop_config)
    controller = EndstopController(endstop, motor_controller_instance)

    # Create new config with different values
    new_config = EndstopConfig(
        pin=5,
        angular_position=180,
        motor_name="updated_motor",
        pull_up=False,
        bounce_time=0.01
    )

    # Apply new settings
    controller._apply_settings(new_config)

    # Check that settings were updated
    assert controller._pin == new_config.pin
    assert controller.model.settings.angular_position == new_config.angular_position
    assert controller.model.settings.motor_name == new_config.motor_name



import asyncio


@pytest.mark.asyncio
async def test_start_stop_listener(endstop_config, motor_controller_instance):
    """Test starting and stopping the event listener."""
    endstop = Endstop(name="test_endstop", settings=endstop_config)
    controller = EndstopController(endstop, motor_controller_instance)

    # Start listener
    task = controller.start_listener()
    assert controller._listener_task is not None
    assert not controller._listener_task.done()

    # Start again should return the same task
    task2 = controller.start_listener()
    assert task is task2

    # Stop listener
    controller.stop_listener()
    await asyncio.sleep(0.1)  # Give time for cancellation to complete
    assert controller._listener_task.done() or controller._listener_task.cancelled()


@pytest.mark.asyncio
async def test_gpio_callback_queues_event(endstop_config, motor_controller_instance):
    """Test that the GPIO callback queues an event."""
    endstop = Endstop(name="test_endstop", settings=endstop_config)
    controller = EndstopController(endstop, motor_controller_instance)

    # Clear the queue
    while not controller._event_queue.empty():
        await controller._event_queue.get()

    # Call the callback
    controller._gpio_callback()

    # Check that an event was queued
    assert not controller._event_queue.empty()
    event = await controller._event_queue.get()
    assert event == "pressed"


@pytest.mark.asyncio
async def test_endstop_integration_with_motor(endstop_config, motor_controller_instance, monkeypatch):
    """Test that the endstop correctly interacts with the motor controller."""
    endstop = Endstop(name="test_endstop", settings=endstop_config)
    controller = EndstopController(endstop, motor_controller_instance)

    # Mock the motor controller's stop method to track calls
    stop_called = False
    original_stop = motor_controller_instance.stop

    def mock_stop():
        nonlocal stop_called
        stop_called = True
        original_stop()

    monkeypatch.setattr(motor_controller_instance, "stop", mock_stop)

    # Start the listener
    task = controller.start_listener()

    # Simulate an endstop trigger
    controller._gpio_callback()

    # Give time for the event to be processed
    await asyncio.sleep(0.5)

    # Check that the motor was stopped
    assert stop_called

    # Check that the motor position was updated
    assert motor_controller_instance.model.angle == endstop_config.angular_position

    # Clean up
    controller.stop_listener()
    await asyncio.sleep(0.1)


def test_device_controller_initializes_endstop_from_nested_config(monkeypatch):
    """
    Integration test to ensure the device module correctly initializes an endstop
    from a nested configuration, preventing regression of the validation error.
    """
    # 1. Create a mock configuration mimicking the real JSON structure
    mock_device_config = {
        "name": "Test Device",
        "model": "mini",
        "shield": "blackshield",
        "cameras": {},
        "motors": {
            "test_motor": {
                "direction_pin": 1, "enable_pin": 2, "step_pin": 3,
                "acceleration": 10000, "max_speed": 5000, "direction": 1,
                "steps_per_rotation": 800
            }
        },
        "lights": {},
        "endstops": {
            "test_endstop": {
                "name": "test_endstop",
                "settings": {
                    "pin": 4,
                    "angular_position": 130,
                    "motor_name": "test_motor",
                    "pull_up": True,
                    "bounce_time": 0.005
                }
            }
        }
    }

    # 2. Mock motor controller dependencies
    mock_motor_controller = MotorController(Motor(name="test_motor", settings=MotorConfig(**mock_device_config["motors"]["test_motor"])))
    monkeypatch.setattr('openscan_firmware.controllers.device.create_motor_controller', lambda motor: None)
    monkeypatch.setattr('openscan_firmware.controllers.device.get_motor_controller', lambda name: mock_motor_controller)

    # 3. Mock the listener to avoid hardware interaction
    monkeypatch.setattr(EndstopController, 'start_listener', lambda self: None)

    # 4. Run the initialization logic from the device module
    try:
        device_controller_module.initialize(mock_device_config)
    except Exception as e:
        pytest.fail(f"device.initialize failed with nested config: {e}")

    # 5. Assert that the endstop was created in the global scanner device object
    scanner_device = device_controller_module._scanner_device
    assert "test_endstop" in scanner_device.endstops
    assert scanner_device.endstops["test_endstop"].name == "test_endstop"
    assert scanner_device.endstops["test_endstop"].settings.pin == 4