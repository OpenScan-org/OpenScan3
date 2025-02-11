from typing import Optional, Dict
from .interfaces import StatefulHardware, ControllerFactory
from app.config.motor import MotorConfig
from app.models.motor import Motor
from app.controllers.hardware import gpio
import time
import math
import asyncio
from concurrent.futures import ThreadPoolExecutor



class MotorController(StatefulHardware):
    _executor = ThreadPoolExecutor(max_workers=4)
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
        self._current_steps = abs(step_count)

        # This function will run in a thread
        def do_movement():
            ramp = self.model.settings.acceleration_ramp
            acc = self.model.settings.acceleration
            delay_init = self.model.settings.delay

            # Set direction
            if step_count > 0:
                gpio.set_pin(self.model.settings.direction_pin, True)
            if step_count < 0:
                gpio.set_pin(self.model.settings.direction_pin, False)

            steps = abs(step_count)
            for x in range(steps):
                gpio.set_pin(self.model.settings.step_pin, True)

                # Calculate acceleration
                if x <= ramp and x <= steps / 2:
                    delay = delay_init * (
                            1 + -1 / acc * math.cos(1 * (ramp - x) / ramp) + 1 / acc
                    )
                elif steps - x <= ramp and x > steps / 2:
                    delay = delay_init * (
                            1 - 1 / acc * math.cos(1 * (ramp + x - steps) / ramp) + 1 / acc
                    )
                else:
                    delay = delay_init

                time.sleep(delay)
                gpio.set_pin(self.model.settings.step_pin, False)
                time.sleep(delay)

            self._current_steps = 0

        # do movement in threads
        await asyncio.get_event_loop().run_in_executor(
            self._executor,
            do_movement
        )

class MotorControllerFactory(ControllerFactory[MotorController, Motor]):
    _controller_class = MotorController