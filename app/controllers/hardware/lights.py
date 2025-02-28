from typing import Optional, Dict, Type

from app.controllers.settings import SettingsManager
from app.models.light import Light, LightConfig

from controllers.hardware import gpio
from controllers.hardware.interfaces import SwitchableHardware, ControllerFactory

class LightController(SwitchableHardware):
    def __init__(self, light: Light):
        self.model = light
        self.settings_manager = SettingsManager(
            light,
            autosave=True,
            on_settings_changed=self._apply_settings_to_hardware
        )
        self.settings_manager.load_from_file()
        self._apply_settings_to_hardware()
        self._is_on = False

    def _apply_settings_to_hardware(self):
        gpio.initialize_pins(self.settings_manager.get_setting("pins"))

    def get_status(self):
        return {
            "name": self.model.name,
            "turned_on": self.is_on(),
            "settings": self.get_config()
        }

    def get_config(self) -> LightConfig:
        return self.settings_manager.get_all_settings()

    def is_on(self) -> bool:
        return self._is_on

    def turn_on(self):
        for pin in self.settings_manager.get_setting("pins"):
            gpio.set_pin(pin, True)
        self._is_on = True

    def turn_off(self):
        for pin in self.settings_manager.get_setting("pins"):
            gpio.set_pin(pin, False)
        self._is_on = False


class LightControllerFactory(ControllerFactory[LightController, Light]):
    @classmethod
    @property
    def _controller_class(cls) -> Type[LightController]:
        return LightController
