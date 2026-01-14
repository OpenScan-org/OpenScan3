import pytest
from gpiozero.pins.mock import MockFactory
from gpiozero import Device


from openscan_firmware.controllers.hardware.lights import LightController
from openscan_firmware.models.light import Light
from openscan_firmware.config.light import LightConfig


Device.pin_factory = MockFactory()

@pytest.fixture
def light_config_with_pins():
    """Provides light config for the tests."""
    return LightConfig(pins=[1,2], pwm_support=True)

@pytest.fixture
def light_config_with_a_pin():
    """Provides light config for the tests."""
    return LightConfig(pin=2, pwm_support=True)

@pytest.fixture
def light_config_with_pin_and_pins_duplicate():
    """Provides light config for the tests."""
    return LightConfig(pin=2, pins=[1,2], pwm_support=True)

@pytest.fixture
def light_config_with_pin_and_pins():
    """Provides light config for the tests."""
    return LightConfig(pin=3, pins=[1,2], pwm_support=True)

LIGHT_CONFIG_FIXTURES = [
    "light_config_with_pins",
    "light_config_with_a_pin",
    "light_config_with_pin_and_pins_duplicate",
    "light_config_with_pin_and_pins",
]

@pytest.mark.parametrize("light_config", LIGHT_CONFIG_FIXTURES)
def test_lightcontroller_initialization(light_config, request):
    """Test LightController initialization."""
    light_config = request.getfixturevalue(light_config)
    light = Light(name="test_light", settings=light_config)
    controller = LightController(light)
    assert controller.model == light
    for field in ["pin", "pins", "pwm_support"]:
        assert getattr(controller.settings._settings, field) == getattr(light_config, field)


@pytest.mark.parametrize("light_config", LIGHT_CONFIG_FIXTURES)
def test_turn_on_light(light_config, request):
    """Test turning on the light."""
    light_config = request.getfixturevalue(light_config)
    light = Light(name="test_light", settings=light_config)
    controller = LightController(light)
    assert not controller.is_on
    controller.turn_on()
    assert controller.is_on is True


def test_change_light_settings(light_config_with_pins):
    """Test if the light settings can be changed and are propagated to light model."""
    light = Light(name="test_light", settings=light_config_with_pins)
    controller = LightController(light)
    controller.settings.pwm_support = False
    assert controller.settings.pwm_support is False
    assert controller.model.settings.pwm_support is False


def test_pin_and_pins_merge(request):
    config = request.getfixturevalue("light_config_with_pin_and_pins")
    assert sorted(config.pins) == [1, 2, 3]

def test_light_config_empty_pins():
    config = LightConfig()
    assert config.pins == []

def test_light_config_duplicate_pin(request):
    config = request.getfixturevalue("light_config_with_pin_and_pins_duplicate")
    assert sorted(config.pins) == [1, 2]

def test_lightcontroller_get_status(light_config_with_pins):
    light = Light(name="test_light", settings=light_config_with_pins)
    controller = LightController(light)
    status = controller.get_status()
    assert isinstance(status, dict)
    assert status["name"] == "test_light"
    assert status["is_on"] is False  # Light not turned on after initializing
    assert isinstance(status["settings"], dict)
    assert status["settings"]["pins"] == light_config_with_pins.pins