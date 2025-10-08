"""
Endstop for motor controller.

This module defines the EndstopController class, which handles an endstop for a motor controller.

When the endstop is triggered, the motor will be stopped immediately and moved back a small distance.
"""

import logging
import time
import asyncio
from asyncio import Queue

from openscan.config.endstop import EndstopConfig
from openscan.models.motor import Endstop
from openscan.controllers.settings import Settings
from openscan.controllers.hardware.gpio import (
    initialize_button,
    register_button_callback,
    remove_button_callback,
    is_button_pressed,
)
from openscan.controllers.hardware.motors import get_motor_controller, MotorController

logger = logging.getLogger(__name__)

class EndstopController:
    """
    Controls an endstop for a motor controller.

    Args:
        endstop (Endstop): Endstop model configuration.
        controller (MotorController): The motor controller to be controlled.

    Attributes:
        model (Endstop): The endstop model configuration.
        settings (Settings): The endstop settings.
        _motor_controller (MotorController): The motor controller to be controlled.
        _pin (int): The GPIO pin number for the endstop.
        _event_queue (Queue): A queue to store events.
        _listener_task (asyncio.Task): The task to listen for button events.
    """
    def __init__(self, endstop: Endstop, controller: MotorController):
        self.model = endstop
        self.settings = Settings(endstop.settings, on_change=self._apply_settings)
        self._motor_controller = controller
        self._pin = endstop.settings.pin
        self._event_queue = Queue(maxsize=10)  # Create a queue for this instance
        self._listener_task = None  # To hold the reference to the listener task

        initialize_button(self._pin, pull_up=self.settings.pull_up, bounce_time=self.settings.bounce_time)
        register_button_callback(self._pin, "when_released", self._gpio_callback)
        self._motor_controller.endstop = self
        logger.info(f"Endstop for motor {self.settings.motor_name} initialized on pin {self._pin}.")


    def _apply_settings(self, config: EndstopConfig):
        """
        Allpy settings for the endstop.

        This method is called when the endstop settings are updated.
        It re-initializes the endstop on the new settings.
        Updates the pin number and re-registers the button callback.

        Args:
            config (EndstopConfig): The new settings for the endstop.
        """
        remove_button_callback(self._pin, "when_released")
        self.model.settings = config
        self._pin = config.pin
        initialize_button(self._pin, pull_up=self.settings.pull_up, bounce_time=self.settings.bounce_time)
        register_button_callback(self._pin, "when_released", self._gpio_callback)
        logger.info(f"Endstop '{self.model.name}' for motor {self.settings.motor_name} re-initialized on pin {self._pin}.")

    def get_config(self) -> EndstopConfig:
        """ Returns the current settings for the endstop.

        Returns:
            EndstopConfig: The current settings for the endstop.
        """
        return self.settings.model


    def get_status(self) -> dict:
        """ Returns the current status of the endstop.

        Returns:
            dict: A dictionary containing the status of the endstop.
        """
        return {"assigned_motor": self.settings.motor_name,
                "position": self.settings.angular_position,
                "pin": self.settings.pin,
                "is_pressed": not is_button_pressed(self.settings.pin)}


    async def _move_back_task(self):
        """Coroutine to handle the reverse movement after endstop press."""
        # Use angular_position for move back distance, ensure sign is correct for moving away
        move_back_degrees = 1 # move just one degree
        await asyncio.sleep(0.2) # Delay to allow motor state to settle
        motor_is_busy = self._motor_controller.is_busy()
        if not motor_is_busy:
            logger.debug(f"Endstop: Motor is not busy. Moving back by {move_back_degrees} degrees.")
            await self._motor_controller.move_degrees(move_back_degrees)


    def _gpio_callback(self):
        """
        Immediate, synchronous callback executed by gpiozero in its thread.
        Only put a marker event into the queue, do not block or call async functions.
        """
        try:
             # Put a simple marker event into the queue
             self._event_queue.put_nowait("pressed")
             logger.debug(f"Endstop '{self.model.name}' raw press detected on pin {self._pin}. Event queued.")
        except asyncio.QueueFull:
             logger.warning(f"Endstop for motor '{self.settings.motor_name}' event queue is full. Event dropped.")
        except Exception as e:
             logger.error(f"Error in endstop GPIO callback for pin {self._pin}: {e}", exc_info=True)


    async def _process_events(self):
        """
        ASYNCHRONOUS task running in the main event loop.
        Waits for events from the queue and handles them.
        """
        while True:
             try:
                  event = await self._event_queue.get()

                  if event == "pressed":
                       logger.info(f"Endstop '{self.model.name}' triggered. "
                                   f"Stopping motor '{self.settings.motor_name}' and moving back...")

                       # 1. Stop the motor immediately (this should be thread-safe)
                       self._motor_controller.stop()
                       await asyncio.sleep(0.1)
                       logger.debug(f"Endstop {self.model.name} stopped motor '{self.settings.motor_name}'.")

                       # 2. Set motor position to the defined endstop angle
                       self._motor_controller.model.angle = self.settings.angular_position
                       # Also reset internal motor state
                       self._motor_controller._target_angle = None # Clear target
                       logger.debug(f"Endstop {self.model.name} set motor '{self.settings.motor_name}' position to {self.settings.angular_position} degrees.")

                       # 3. Move back slightly (asynchronously)
                       # Small delay before moving back, allows system to settle
                       await asyncio.sleep(0.1)
                       await self._motor_controller.move_degrees(-2)
                       logger.debug(f"Endstop {self.model.name} moved motor '{self.settings.motor_name}' back by 2 degrees.")

                  # Mark the task as done
                  self._event_queue.task_done()

             except asyncio.CancelledError:
                  logger.error("Event processor for Endstop cancelled.")
                  break # Exit loop if task is cancelled
             except Exception as e:
                  logger.error(f"Error in event processor for Endstop: {e}", exc_info=True)
                  # Avoid busy-looping on unexpected errors; wait a bit before retrying
                  await asyncio.sleep(1)


    def start_listener(self):
        """
        Starts the asynchronous event listener task.
        Should be called once from the main async context after initialization.

        Returns:
            asyncio.Task: The listener task.
        """
        if self._listener_task is None or self._listener_task.done():
             logger.debug(f"Starting event listener for Endstop '{self.settings.motor_name}'...")
             self._listener_task = asyncio.create_task(self._process_events())
             return self._listener_task
        else:
             logger.debug(f"Listener task for Endstop '{self.settings.motor_name}' already running.")
             return self._listener_task


    def stop_listener(self):
        """
        Stops the asynchronous event listener task gracefully.
        """
        if self._listener_task and not self._listener_task.done():
             logger.debug(f"Stopping event listener for Endstop '{self.settings.motor_name}'...")
             self._listener_task.cancel()
             # Optionally wait for the task to finish cancelling
             # await asyncio.wait([self._listener_task], timeout=1.0)
        else:
             logger.debug(f"Listener task for Endstop '{self.settings.motor_name}' already stopped or not started.")
