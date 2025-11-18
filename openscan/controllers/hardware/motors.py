"""Motor Controller

This module provides a MotorController class for controlling motors.
It implements the StatefulHardware interface to manage the state of the motor.
Currently supporting only stepper motors.
Possible angles for a given motor are limited by min_angle and max_angle and can be between 0 and 360 degrees.

OpenScan Mini:
If the camera is in horizontal position, the rotor motor is at an 90 degrees angle.
The camera is in (hypothetical) vertical position facing down, the rotor motor is at a 0 degrees angle.

"""

import logging
from typing import Optional, Dict, List
import time
import math
import asyncio
from concurrent.futures import ThreadPoolExecutor

from openscan.controllers.hardware.interfaces import StatefulHardware, create_controller_registry
from openscan.config.motor import MotorConfig
from openscan.models.motor import Motor
from openscan.controllers.hardware import gpio
from openscan.controllers.settings import Settings
from openscan.controllers.services.device_events import (
    notify_busy_change,
    schedule_device_status_broadcast,
)
from openscan.models.paths import PolarPoint3D, PathMethod


logger = logging.getLogger(__name__)

class MotorController(StatefulHardware):
    """Motor controller

    Attributes:
        model (Motor): The motor model.
        settings (Settings): The settings for the motor.
        _current_steps (int): The current number of steps the motor has moved.
        _target_angle (float): The target angle for the motor.
        _stop_requested (bool): Flag to indicate if a stop request has been made.
        endstop (Endstop): The endstop for the motor."""
    _executor = ThreadPoolExecutor(max_workers=4)
    def __init__(self, motor: Motor):
        self.model = motor
        self.settings = Settings(
            motor.settings,
            on_change=self._on_settings_change
        )
        self._current_steps = 0
        self._target_angle = None
        self._stop_requested = False
        self.endstop = None
        self._apply_settings_to_hardware(self.settings.model)
        logger.debug(f"Motor controller for '{self.model.name}' initialized.")

    def _on_settings_change(self, settings: MotorConfig) -> None:
        self._apply_settings_to_hardware(settings)
        schedule_device_status_broadcast([f"motors.{self.model.name}.settings"])

    def _apply_settings_to_hardware(self, settings: MotorConfig):
        # update model settings
        self.model.settings = settings

        # apply to hardware
        gpio.initialize_output_pins([
            self.settings.direction_pin,
            self.settings.step_pin,
            self.settings.enable_pin
        ])

        logger.info(f"Motor '{self.model.name}' settings updated.")

    def is_busy(self) -> bool:
        """Check if the motor is busy (moving)."""
        return self._current_steps > 0


    def get_status(self) -> dict:
        status = {
            "name": self.model.name,
            "angle": self.model.angle,
            "busy": self.is_busy(),
            "target_angle": self._target_angle,
            "settings": self.get_config(),
            "endstop": None
        }
        if self.endstop is not None:
            status["endstop"] = self.endstop.get_status()
        return status


    def get_config(self) -> MotorConfig:
        return self.settings.model


    def stop(self) -> None:
        """Request to stop the current motor movement."""
        self._stop_requested = True
        logger.info(f"Motor '{self.model.name}' stop requested.")

    def _normalize_target_angle(self, desired_angle: float) -> float:
        """Normalize the target angle to the range [min_angle, max_angle].

        Args:
            desired_angle: Target angle in degrees.

        Returns:
            Normalized target angle in degrees.
        """
        min_limit = self.settings.min_angle
        max_limit = self.settings.max_angle

        if min_limit == 0.0 and max_limit == 360.0:
            return desired_angle % 360

        final_angle = desired_angle  # Start with the raw desired angle

        if final_angle < min_limit:
            logger.warning(f"Warning: Desired angle {desired_angle:.2f} is below minimum limit {min_limit:.2f}. "
                  f"Clamping to {min_limit:.2f}.")
            final_angle = min_limit
        elif final_angle > max_limit:
            logger.warning(f"Warning: Desired angle {desired_angle:.2f} is above maximum limit {max_limit:.2f}. "
                  f"Clamping to {max_limit:.2f}.")
            final_angle = max_limit

        # If desired_angle was already within [min_limit, max_limit],
        # final_angle remains unchanged, and no warning is printed.
        logger.debug(f"Normalized target angle: {final_angle:.2f}.")
        return final_angle

    def estimate_movement_time(self, steps: int) -> float:
        """
        Estimate the total time required to execute a movement with the given number of steps.

        Args:
            steps: Number of steps for the movement (absolute value will be used)

        Returns:
            Estimated time in seconds for the movement
        """
        steps = abs(steps)
        if steps == 0:
            return 0.0

        max_accel = self.settings.acceleration  # Steps/second^2
        max_speed = self.settings.max_speed  # Steps/second

        # Calculate acceleration distance (steps)
        accel_time = max_speed / max_accel  # Time to reach max speed
        accel_steps = int(0.5 * max_accel * accel_time * accel_time)  # s = 1/2 * a * t^2

        # Check if we can reach max speed (trapezoidal vs. triangular profile)
        if 2 * accel_steps > steps:
            # Triangular profile - never reach max speed
            accel_steps = steps // 2
            if accel_steps < 1:
                accel_steps = 1

            # Calculate peak speed and time for triangular profile
            peak_time = math.sqrt(2 * accel_steps / max_accel)
            # Total time is 2 * peak_time (acceleration + deceleration)
            total_time = 2 * peak_time
        else:
            # Trapezoidal profile - we reach max speed
            const_steps = steps - (2 * accel_steps)

            # Time for acceleration phase
            accel_time = max_speed / max_accel

            # Time for constant speed phase
            const_time = const_steps / max_speed if const_steps > 0 else 0

            # Time for deceleration phase (same as acceleration)
            decel_time = accel_time

            # Total time
            total_time = accel_time + const_time + decel_time

        return total_time

    def estimate_movement_time_for_degrees(self, degrees: float) -> float:
        """
        Estimate the total time required to move the motor by a given number of degrees.

        Args:
            degrees: Number of degrees to move

        Returns:
            Estimated time in seconds for the movement
        """
        spr = self.settings.steps_per_rotation
        step_count = int(abs(degrees) * spr / 360)
        return self.estimate_movement_time(step_count)

    def estimate_move_to_time(self, target_degrees: float) -> float:
        """
        Estimate the total time required to move the motor to an absolute position.

        Args:
            target_degrees: Target absolute position in degrees

        Returns:
            Estimated time in seconds for the movement
        """
        target_angle = self._normalize_target_angle(target_degrees % 360)
        current_angle = self.model.angle
        move_angles = target_angle - current_angle

        # Calculate shortest path (same logic as move_to_target_angle method)
        if move_angles > 180:
            move_angles -= 360
        elif move_angles < -180:
            move_angles += 360

        return self.estimate_movement_time_for_degrees(abs(move_angles))

    async def move_to(self, degrees: float) -> None:
        """Move motor to absolute position"""
        if self.is_busy():
            raise RuntimeError("Motor is busy")

        self._stop_requested = False  # Reset stop flag before moving

        target_angle = self._normalize_target_angle(degrees % 360)
        await self._move_to_target_angle(target_angle)


    async def move_degrees(self, degrees: float) -> None:
        """Move motor by degrees

        Args:
            degrees: Number of degrees to move"""
        if self.is_busy():
            raise RuntimeError("Motor is busy")

        self._stop_requested = False # Reset stop flag before moving

        target_angle = self._normalize_target_angle(self.model.angle + degrees)
        await self._move_to_target_angle(target_angle)


    async def _move_to_target_angle(self, target_angle: float) -> None:
        """Internal method to move motor to target angle.

        Args:
            target_angle: Target angular position in degrees"""

        logger.debug(f"Will move motor {self.model.name} to target angle: {target_angle}")

        spr = self.settings.steps_per_rotation
        direction = self.settings.direction

        degrees_to_move = target_angle - self.model.angle


        # Optional: take the shortest path (for full 360°)
        if self.settings.min_angle == 0.0 and self.settings.max_angle == 360.0:
            if degrees_to_move > 180:
                degrees_to_move -= 360
            elif degrees_to_move < -180:
                degrees_to_move += 360

        step_count = int(degrees_to_move * spr / 360) * direction
        logger.debug(f"Motor {self.model.name} will move {step_count} steps.")
        await self._execute_movement(step_count, target_angle)

    def _pre_calculate_step_times(self, steps: int, min_interval=0.0001) -> List[float]:
        """
        Pre-calculate the exact time for each step in the movement.
        Returns a list of absolute timestamps (relative to start time) when steps should occur.

        Args:
            steps: Number of steps to move
            min_interval: Minimum interval between steps in seconds (prevents too rapid stepping)
        """
        max_accel = self.settings.acceleration  # Steps/second^2
        max_speed = self.settings.max_speed  # Steps/second

        # Calculate acceleration distance (steps)
        accel_time = max_speed / max_accel  # Time to reach max speed
        accel_steps = int(0.5 * max_accel * accel_time * accel_time)  # s = 1/2 * a * t^2

        # Check if we can reach max speed (trapezoidal vs. triangular profile)
        if 2 * accel_steps > steps:
            # Triangular profile
            accel_steps = steps // 2
            if accel_steps < 1:
                accel_steps = 1

            # Recalculate peak speed
            peak_time = math.sqrt(2 * accel_steps / max_accel)
            peak_speed = max_accel * peak_time
        else:
            # Trapezoidal profile - we reach max speed
            peak_speed = max_speed
            peak_time = accel_time

        # Calculate constant speed distance
        const_steps = steps - (2 * accel_steps)
        const_time = const_steps / max_speed if const_steps > 0 else 0

        # Generate step times array
        step_times = []

        # Function to calculate time for a given step in acceleration/deceleration phase
        def time_for_accel_step(step):
            return math.sqrt(2 * step / max_accel)

        # 1. Acceleration phase
        for step in range(accel_steps):
            step_times.append(time_for_accel_step(step + 1))

        # 2. Constant speed phase
        if const_steps > 0:
            constant_interval = 1.0 / max_speed
            # Ensure we don't have steps too close together
            constant_interval = max(constant_interval, min_interval)

            for step in range(const_steps):
                step_times.append(peak_time + (step + 1) * constant_interval)

        # 3. Deceleration phase
        total_steps_before_decel = accel_steps + const_steps
        for step in range(accel_steps):
            decel_step = accel_steps - step - 1  # Count down
            # Time from start of deceleration
            decel_time = time_for_accel_step(accel_steps) - time_for_accel_step(decel_step)
            # Add to total time (acceleration + constant + deceleration)
            total_time = peak_time + const_time + decel_time
            step_times.append(total_time)

        # Post-process: Ensure minimum time between steps
        # This prevents steps from being too close together due to rounding errors
        for i in range(1, len(step_times)):
            if step_times[i] - step_times[i - 1] < min_interval:
                step_times[i] = step_times[i - 1] + min_interval

        return step_times

    async def _execute_movement(self, step_count: int, requested_degrees: float) -> None:
        """Execute the movement using pre-calculated step timings
        Args:
            step_count: Number of steps to move"""
        self._current_steps = abs(step_count)
        notify_busy_change("motors", self.model.name)

        # This function will run in a thread
        def do_movement() -> int:
            # Set direction
            if step_count > 0:
                gpio.set_output_pin(self.settings.direction_pin, True)
            else:
                gpio.set_output_pin(self.settings.direction_pin, False)

            steps = abs(step_count)
            if steps == 0:
                return 0

            # Pre-calculate the timing for each step - ensure minimum 100μs between steps
            step_times = self._pre_calculate_step_times(steps, min_interval=0.0001)

            # Use a consistent pulse width
            pulse_width = 0.000010  # 10 microseconds

            # Execute the movement
            executed_steps = 0
            start_time = time.time()

            # Batch steps for efficiency if steps are very close together
            i = 0
            batch_size = 16  # Max steps to batch check

            while i < len(step_times):
                # Check for stop request
                if self._stop_requested:
                    logger.debug(f"Motor {self.model.name}: Stop requested after {executed_steps} steps.")
                    break

                # Current time relative to start
                current_time = time.time() - start_time

                # Check all steps in current batch window
                next_step_idx = None
                for j in range(i, min(i + batch_size, len(step_times))):
                    if current_time >= step_times[j]:
                        next_step_idx = j

                if next_step_idx is not None:
                    # Execute all steps that should have happened by now
                    for j in range(i, next_step_idx + 1):
                        # Take a step with clean pulse
                        gpio.set_output_pin(self.settings.step_pin, True)
                        time.sleep(pulse_width)
                        gpio.set_output_pin(self.settings.step_pin, False)
                        executed_steps += 1

                    # Update index to next position
                    i = next_step_idx + 1

                    # Add a small delay after pulse to avoid overwhelming the GPIO
                    time.sleep(0.000010)  # 10μs
                else:
                    # No steps ready yet, calculate time to next step
                    if i < len(step_times):
                        wait_time = step_times[i] - current_time

                        # Use a variable sleep strategy:
                        # - For longer waits, use standard sleep
                        # - For very short waits, use a shorter sleep
                        if wait_time > 0.001:  # >1ms
                            time.sleep(0.9 * wait_time)  # Sleep for 90% of wait time
                        else:
                            # For very short waits, just do a minimal sleep
                            time.sleep(0.00005)  # 50μs
                    else:
                        # No more steps
                        break

            return executed_steps

        try:
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
            logger.debug(f"Motor {self.model.name} moved {executed_degrees:.2f} degrees.")
        except asyncio.CancelledError:
            logger.info(f"Motor {self.model.name} movement cancelled.")
            raise
        finally:
            # CRITICAL: Always reset busy state, even if cancelled
            self._current_steps = 0
            self._target_angle = None
            notify_busy_change("motors", self.model.name)

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


async def move_to_point(point: PolarPoint3D):
    """Move motors to specified polar coordinates"""
    # Get motor controllers
    turntable = get_motor_controller("turntable")
    rotor = get_motor_controller("rotor")

    # Calculate adaptive timeouts based on estimated movement times
    # If motors are busy, we don't know exactly what movement they're doing,
    # so we use a reasonable grace period based on typical movements
    max_typical_movement_time = max(   # Worst case: half rotation
        turntable.estimate_movement_time_for_degrees(180),
        rotor.estimate_movement_time_for_degrees(180)
    )
    
    # Adaptive timeouts based on motor capabilities
    graceful_wait = max(2.0, max_typical_movement_time * 1.5)  # 1.5x estimated time, min 2s
    total_timeout = max(10.0, max_typical_movement_time * 3.0)  # 3x estimated time, min 10s
    
    logger.debug(f"Using adaptive timeouts: graceful={graceful_wait:.1f}s, total={total_timeout:.1f}s")
    
    start_time = asyncio.get_event_loop().time()
    
    while turntable.is_busy() or rotor.is_busy():
        current_time = asyncio.get_event_loop().time()
        elapsed = current_time - start_time
        
        if elapsed > graceful_wait:
            # After graceful wait, try to stop motors
            logger.info(f"Motors still busy after {graceful_wait:.1f}s graceful wait, requesting stop...")
            turntable.stop()
            rotor.stop()
            
            # Give motors a moment to respond to stop
            await asyncio.sleep(0.2)
            
            # Continue waiting but with total timeout
            if elapsed > total_timeout:
                logger.error(f"Timeout waiting for motors after {total_timeout:.1f}s. Motors may be stuck.")
                if turntable.is_busy() or rotor.is_busy():
                    raise RuntimeError(f"Motors failed to stop after {total_timeout:.1f}s timeout. Turntable busy: {turntable.is_busy()}, Rotor busy: {rotor.is_busy()}")
                break
        
        # Log less frequently to reduce spam
        if int(elapsed * 10) % 10 == 0:  # Log every 100ms
            logger.debug("Waiting for motors to be ready")
        await asyncio.sleep(0.01)

    # Move both motors concurrently to specified point
    await asyncio.gather(
        turntable.move_to(point.fi),
        rotor.move_to(point.theta)
    )

    logger.debug(f"Moved to {point}")