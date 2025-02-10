from typing import Optional, Dict
from .interfaces import StatefulHardware
from app.config.motor import MotorConfig
from app.models.motor import Motor
from app.controllers.hardware import gpio
import time
import math


class MotorController(StatefulHardware):
    def __init__(self, motor: Motor):
        self.model = motor
        self._current_steps = 0
        self._target_angle = None
        gpio.initialize_pins([self.model.settings.direction_pin, self.model.settings.step_pin, self.model.settings.enable_pin])

    def is_busy(self) -> bool:
        return self._current_steps > 0

    def get_status(self) -> dict:
        return {
            "name": self.model.name,
            "angle": self.model.angle,
            "busy": self.is_busy(),
            "target_angle": self._target_angle
        }

    def get_config(self) -> MotorConfig:
        return self.model.settings

    async def move_to(self, degrees: float) -> None:
        """Move motor to absolute position"""
        if self.is_busy():
            raise RuntimeError("Motor is busy")

        self._target_angle = degrees % 360
        move_angles = self._target_angle - self.model.angle
        await self.move_degrees(move_angles)


    async def move_degrees(self, degrees: float) -> None:
        """Internal method for relative movement"""

        spr = self.model.settings.steps_per_rotation
        dir = self.model.settings.direction
        step_count = int(degrees * spr / 360) * dir
        try:
            await self._execute_movement(step_count)
        finally:
            self.model.angle = (self.model.angle + degrees) % 360

    async def _execute_movement(self, step_count: int) -> None:
        """Execute the actual movement with acceleration"""

        ramp = self.model.settings.acceleration_ramp
        acc = self.model.settings.acceleration
        delay_init = self.model.settings.delay
        delay = delay_init



        if step_count > 0:
            gpio.set_pin(self.model.settings.direction_pin, True)
        if step_count < 0:
            gpio.set_pin(self.model.settings.direction_pin, False)
            step_count = -step_count
        for x in range(step_count):
            gpio.set_pin(self.model.settings.step_pin, True)
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
            gpio.set_pin(self.model.settings.step_pin, False)
            time.sleep(delay)

class MotorControllerFactory:
    _controllers: Dict[str, MotorController] = {}

    @classmethod
    def get_controller(cls, motor: Motor) -> MotorController:
        if motor.name not in cls._controllers:
            cls._controllers[motor.name] = MotorController(motor)
        return cls._controllers[motor.name]

    @classmethod
    def get_all_controllers(cls) -> Dict[str, MotorController]:
        return cls._controllers.copy()