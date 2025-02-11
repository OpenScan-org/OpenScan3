from typing import Optional, Dict

from app.models.light import Light, LightConfig

from controllers.hardware import gpio
from controllers.hardware.interfaces import SwitchableHardware, ControllerFactory

class LightController(SwitchableHardware):
    def __init__(self, light: Light):
        self.model = light
        self._is_on = False
        gpio.initialize_pins(light.settings.pins)

    def get_status(self):
        return {
            "name": self.model.name,
            "turned_on": self.is_on()
        }

    def get_config(self) -> LightConfig:
        return self.model.settings

    def is_on(self) -> bool:
        return self._is_on

    def turn_on(self):
        for pin in self.model.settings.pins:
            gpio.set_pin(pin, True)
        self._is_on = True

    def turn_off(self):
        for pin in self.model.settings.pins:
            gpio.set_pin(pin, False)
        self._is_on = False


class LightControllerFactory(ControllerFactory[LightController, Light]):
    _controller_class = LightController
