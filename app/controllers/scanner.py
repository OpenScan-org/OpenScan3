import time

from app.config import config
from app.controllers import gpio


def start_scan():
    ...


def toggle_lights():
    ...


def lights_on():
    ...


def lights_off():
    ...


def trigger_external_cam():
    gpio.set_pin(config.external_camera_pin, True)
    time.sleep(config.external_camera_delay)
    gpio.set_pin(config.external_camera_pin, False)
