from typing import Optional

from app.config import config
from app.config.light import LightConfig
from app.models.light import Light, LightType

from app.controllers import gpio

def get_lights() -> dict[LightType, Light]:
    return config.lights

def get_light(light_type: LightType) -> Optional[Light]:
    return config.lights.get(light_type)

def turn_light_on(light: Light):
    for pin in light.settings.pins:
        gpio.set_pin(pin, True)

def turn_light_off(light: Light):
    for pin in light.settings.pins:
        gpio.set_pin(pin, False)