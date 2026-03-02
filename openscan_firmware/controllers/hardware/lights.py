"""
Light controller.

Implements the `SwitchableHardware` interface for controlling lights.
Currently supporting ring light without PWM.
"""

import logging

from typing import Callable, Awaitable

from openscan_firmware.controllers.settings import Settings
from openscan_firmware.models.light import Light, LightConfig

from openscan_firmware.controllers.hardware import gpio
from openscan_firmware.controllers.hardware.interfaces import HardwareEvent, SwitchableHardware, SleepCapableHardware, create_controller_registry
from openscan_firmware.controllers.services.device_events import schedule_device_status_broadcast

logger = logging.getLogger(__name__)

class LightController(SwitchableHardware, SleepCapableHardware):
    def __init__(self, light: Light):
        self.model = light
        self.settings = Settings(
            light.settings,
            on_change=self._apply_settings_to_hardware
        )
        self._is_on = False
        self._apply_settings_to_hardware(self.settings.model)
        logger.debug(f"Light controller for '{self.model.name}' initialized.")

        # light disabled at startup
        self._enabled = False
        
        # no idle callbacks
        self.is_idle = lambda: True
        self.send_event = None
        
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
        schedule_device_status_broadcast([f"lights.{self.model.name}.settings"])

    def get_status(self):
        return {
            "name": self.model.name,
            "is_on": self.is_on,
            "settings": self.get_config().model_dump()
        }

    def get_config(self) -> LightConfig:
        return self.settings.model

    def refresh(self):
        if self.is_idle():
            logger.info(f"Light '{self.model.name}' idle.")
            for pin in self.settings.pins:
                gpio.set_output_pin(pin, False)
        else:
            logger.info(f"Light '{self.model.name}' active.")
            for pin in self.settings.pins:
                gpio.set_output_pin(pin, self._is_on)
           
            
    def setIdleCallbacks(self, is_idle: Callable[[], bool], send_event: Callable[[HardwareEvent], Awaitable[None]]) -> None:
        self.is_idle = is_idle
        self.send_event = send_event

    @property
    def is_on(self) -> bool:
        return self._is_on

    async def turn_on(self):
        self._is_on = True
        #resume from idle
        if self.is_idle():
            logger.info("Device idle, must exit before")
            await self.send_event(HardwareEvent.LIGHT_EVENT)
        else:
            self.refresh()
        logger.info(f"Light '{self.model.name}' turned on.")
        schedule_device_status_broadcast([f"lights.{self.model.name}.is_on"])

    async def turn_off(self):
        self._is_on = False
        #resume from idle
        if self.is_idle():
            logger.info("Device idle, must exit before")
            await self.send_event(HardwareEvent.LIGHT_EVENT)
        else:
            self.refresh()
        logger.info(f"Light '{self.model.name}' turned off.")
        schedule_device_status_broadcast([f"lights.{self.model.name}.is_on"])


create_light_controller, get_light_controller, remove_light_controller, _light_registry = create_controller_registry(LightController)


def get_all_light_controllers():
    """Get all currently registered light controllers"""
    return _light_registry.copy()
