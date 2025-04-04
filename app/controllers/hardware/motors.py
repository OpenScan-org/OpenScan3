from typing import Optional, Dict
import time
import math
import asyncio
from concurrent.futures import ThreadPoolExecutor

from .interfaces import StatefulHardware, create_controller_registry
from app.config.motor import MotorConfig
from app.models.motor import Motor
from app.controllers.hardware import gpio
from ..settings import Settings


class MotorController(StatefulHardware):
    _executor = ThreadPoolExecutor(max_workers=4)
    def __init__(self, motor: Motor):
        self.model = motor
        self.settings = Settings(
            motor.settings,
            on_change=self._apply_settings_to_hardware
        )
        self._current_steps = 0
        self._target_angle = None
        self._stop_requested = False
        self._apply_settings_to_hardware(self.settings.model)



    def _apply_settings_to_hardware(self, settings: MotorConfig):
        # update model settings
        self.model.settings = settings

        # apply to hardware
        gpio.initialize_output_pins([
            self.settings.direction_pin,
            self.settings.step_pin,
            self.settings.enable_pin
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
        return self.settings.model

    def stop(self) -> None:
        """Request to stop the current motor movement."""
        self._stop_requested = True

    async def move_to(self, degrees: float) -> None:
        """Move motor to absolute position"""
        if self.is_busy():
            raise RuntimeError("Motor is busy")

        self._stop_requested = False # Reset stop flag before moving
        self._target_angle = degrees % 360
        move_angles = self._target_angle - self.model.angle
        await self.move_degrees(move_angles)


    async def move_degrees(self, degrees: float) -> None:
        """Move motor by degrees"""
        if self.is_busy():
            raise RuntimeError("Motor is busy")

        self._stop_requested = False # Reset stop flag before moving


        spr = self.settings.steps_per_rotation
        direction = self.settings.direction
        step_count = int(degrees * spr / 360) * direction
        await self._execute_movement(step_count, degrees) # Pass spr for angle calculation

        #try:
        #    await self._execute_movement(step_count)
        #finally:
        #    self.model.angle = (self.model.angle + degrees) % 360

    async def _execute_movement(self, step_count: int, requested_degrees: float) -> None:
        """Execute the actual movement with acceleration"""
        self._current_steps = abs(step_count)

        # This function will run in a thread
        def do_movement() -> int:
            ramp = self.settings.acceleration_ramp
            acc = self.settings.acceleration
            delay_init = self.settings.delay
            executed_steps = 0  # Track executed steps

            # Set direction
            if step_count > 0:
                gpio.set_output_pin(self.settings.direction_pin, True)
            if step_count < 0:
                gpio.set_output_pin(self.settings.direction_pin, False)

            steps = abs(step_count)
            for x in range(steps):
                # Check for stop request
                if self._stop_requested:
                    print(f"Motor {self.model.name}: Stop requested after {x} steps.")
                    break # Exit loop if stop is requested

                gpio.set_output_pin(self.settings.step_pin, True)

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
                gpio.set_output_pin(self.settings.step_pin, False)
                time.sleep(delay)
                executed_steps += 1

            return executed_steps
            #self._current_steps = 0

        # Run movement in thread and get actual steps executed
        actual_executed_steps = await asyncio.get_event_loop().run_in_executor(
            self._executor,
            do_movement
        )

        # Calculate executed degrees based on actual steps
        spr = self.settings.steps_per_rotation
        dir_multiplier = 1 if step_count >= 0 else -1
        executed_degrees = (actual_executed_steps / spr * 360) * dir_multiplier * self.settings.direction

        # Update the angle based on actual executed movement
        self.model.angle = (self.model.angle + executed_degrees) % 360
        self._current_steps = 0 # Reset step counter after movement completion/stop
        self._target_angle = None # Reset target angle


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