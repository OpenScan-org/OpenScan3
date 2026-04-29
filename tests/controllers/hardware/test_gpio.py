import pytest

from openscan_firmware.controllers.hardware import gpio as gpio_module


class _FakeDigitalOutputDevice:
    def __init__(self, pin: int, initial_value: bool = False):
        self.pin = pin
        self.value = bool(initial_value)

    def toggle(self):
        self.value = not self.value

    def close(self):
        return None


@pytest.fixture(autouse=True)
def reset_gpio_state(monkeypatch):
    original_outputs = gpio_module._output_pins.copy()
    original_buttons = gpio_module._buttons.copy()

    gpio_module._output_pins.clear()
    gpio_module._buttons.clear()
    monkeypatch.setattr(gpio_module, "DigitalOutputDevice", _FakeDigitalOutputDevice)

    yield

    gpio_module._output_pins.clear()
    gpio_module._buttons.clear()
    gpio_module._output_pins.update(original_outputs)
    gpio_module._buttons.update(original_buttons)


def test_set_output_pin_auto_initializes_when_pin_is_free():
    result = gpio_module.set_output_pin(10, True, auto_initialize=True)

    assert result is True
    assert 10 in gpio_module._output_pins
    assert gpio_module.get_output_pin(10) is True


def test_set_output_pin_rejects_pin_initialized_as_button():
    gpio_module._buttons[10] = object()

    with pytest.raises(ValueError, match="initialized as button input"):
        gpio_module.set_output_pin(10, True, auto_initialize=True)


def test_set_output_pin_requires_initialized_output_without_auto_init():
    with pytest.raises(ValueError, match="not initialized as output"):
        gpio_module.set_output_pin(11, True)
