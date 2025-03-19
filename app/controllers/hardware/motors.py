from typing import Optional, Dict
import time
import math
import asyncio
from concurrent.futures import ThreadPoolExecutor

from .interfaces import StatefulHardware, create_controller_registry
from app.config.motor import MotorConfig
from app.models.motor import Motor
from app.controllers.hardware import gpio
from ..settings import SettingsManager


class MotorController(StatefulHardware):
    _executor = ThreadPoolExecutor(max_workers=4)
    def __init__(self, motor: Motor):
        self.model = motor
        self.settings_manager = SettingsManager(
            motor,
            autosave=True,
            on_settings_changed=self._apply_settings_to_hardware
        )
        self._current_steps = 0
        self._target_angle = None
        self._apply_settings_to_hardware()



    def _apply_settings_to_hardware(self):
        gpio.initialize_pins([
            self.settings_manager.get_setting("direction_pin"),
            self.settings_manager.get_setting("step_pin"),
            self.settings_manager.get_setting("enable_pin")
        ])

    def is_busy(self) -> bool:
        return self._current_steps > 0

    def get_status(self) -> dict:
        return {
            "name": self.model.name,
            "angle": self.model.angle,
            "busy": self.is_busy(),
            "target_angle": self._target_angle,
            "settings": self.get_config()
        }

    def get_config(self) -> MotorConfig:
        return self.settings_manager.get_all_settings()

    async def move_to(self, degrees: float) -> None:
        """Move motor to absolute position"""
        if self.is_busy():
            raise RuntimeError("Motor is busy")

        self._target_angle = degrees % 360
        move_angles = self._target_angle - self.model.angle
        await self.move_degrees(move_angles)


    async def move_degrees(self, degrees: float) -> None:
        """Internal method for relative movement"""

        spr = self.settings_manager.get_setting("steps_per_rotation")
        dir = self.settings_manager.get_setting("direction")
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
            ramp = self.settings_manager.get_setting("acceleration_ramp")
            acc = self.settings_manager.get_setting("acceleration")
            delay_init = self.settings_manager.get_setting("delay")

            # Set direction
            if step_count > 0:
                gpio.set_pin(self.settings_manager.get_setting("direction_pin"), True)
            if step_count < 0:
                gpio.set_pin(self.settings_manager.get_setting("direction_pin"), False)

            steps = abs(step_count)
            for x in range(steps):
                gpio.set_pin(self.settings_manager.get_setting("step_pin"), True)

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
                gpio.set_pin(self.settings_manager.get_setting("step_pin"), False)
                time.sleep(delay)

            self._current_steps = 0

        # do movement in threads
        await asyncio.get_event_loop().run_in_executor(
            self._executor,
            do_movement
        )


create_motor_controller, get_motor_controller, remove_motor_controller, _motor_registry = create_controller_registry(MotorController)


def get_all_motor_controllers():
    """Get all currently registered motor controllers"""
    return _motor_registry.copy()


async def move_motor_to(name: str, position: float) -> None:
    """Move a motor to a position by name"""
    controller = get_motor_controller(name)
    await controller.move_to(position)


def is_motor_busy(name: str) -> bool:
    """Check if a motor is busy"""
    try:
        return get_motor_controller(name).is_busy()
    except ValueError:
        return False