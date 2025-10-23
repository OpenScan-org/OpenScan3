"""
Light controller.

Implements the `SwitchableHardware` interface for controlling lights.
Currently supporting ring light without PWM.
"""

import logging

from openscan.controllers.settings import Settings
from openscan.models.light import Light, LightConfig

from openscan.controllers.hardware import gpio
from openscan.controllers.hardware.interfaces import SwitchableHardware, create_controller_registry

logger = logging.getLogger(__name__)

class LightController(SwitchableHardware):
    def __init__(self, light: Light):
        self.model = light
        self.settings = Settings(
            light.settings,
            on_change=self._apply_settings_to_hardware
        )
        self._is_on = False
        self._apply_settings_to_hardware(self.settings.model)
        logger.debug(f"Light controller for '{self.model.name}' initialized.")

    def _apply_settings_to_hardware(self, settings: LightConfig):
        """Apply settings to hardware and preserve light state."""
        self.model.settings = settings

        was_on = self._is_on
        if was_on:
            self.turn_off()

        gpio.initialize_output_pins(self.settings.pins)

        if was_on:
            self.turn_on()

        logger.info(f"Light '{self.model.name}' settings updated.")

    def get_status(self):
        return {
            "name": self.model.name,
            "is_on": self.is_on,
            "settings": self.get_config().model_dump()
        }

    def get_config(self) -> LightConfig:
        return self.settings.model

    @property
    def is_on(self) -> bool:
        return self._is_on

    def turn_on(self):
        for pin in self.settings.pins:
            gpio.set_output_pin(pin, True)
        self._is_on = True
        logger.info(f"Light '{self.model.name}' turned on.")

    def turn_off(self):
        for pin in self.settings.pins:
            gpio.set_output_pin(pin, False)
        self._is_on = False
        logger.info(f"Light '{self.model.name}' turned off.")


create_light_controller, get_light_controller, remove_light_controller, _light_registry = create_controller_registry(LightController)


def get_all_light_controllers():
    """Get all currently registered light controllers"""
    return _light_registry.copy()
