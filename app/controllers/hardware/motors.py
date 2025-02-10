import time
import math

from typing import Optional

from app.config import config
from app.models.motor import Motor, MotorType

from controllers.hardware import gpio


def get_motors() -> dict[MotorType, Motor]:
    return config.motors


def get_motor(motor_type: MotorType) -> Optional[Motor]:
    return config.motors.get(motor_type)


def move_motor_to(motor: Motor, degrees: float):
    degrees %= 360
    move_angles = degrees - motor.angle
    move_motor_degrees(motor, move_angles)


def move_motor_degrees(motor: Motor, degrees: float):
    spr = motor.settings.steps_per_rotation
    dir = motor.settings.direction
    ramp = motor.settings.acceleration_ramp
    acc = motor.settings.acceleration
    delay_init = motor.settings.delay
    delay = delay_init

    step_count = int(degrees * spr / 360) * dir

    if step_count > 0:
        gpio.set_pin(motor.settings.direction_pin, True)
    if step_count < 0:
        gpio.set_pin(motor.settings.direction_pin, False)
        step_count = -step_count
    for x in range(step_count):
        gpio.set_pin(motor.settings.step_pin, True)
        if x <= ramp and x <= step_count / 2:
            delay = delay_init * (
                1 + -1 / acc * math.cos(1 * (ramp - x) / ramp) + 1 / acc
            )
        elif step_count - x <= ramp and x > step_count / 2:
            delay = delay_init * (
                1 - 1 / acc * math.cos(1 * (ramp + x - step_count) / ramp) + 1 / acc
            )
        else:
            delay = delay_init
        time.sleep(delay)
        gpio.set_pin(motor.settings.step_pin, False)
        time.sleep(delay)

    motor.angle = (motor.angle + degrees) % 360
