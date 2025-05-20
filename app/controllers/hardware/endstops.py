import time
import asyncio
from asyncio import Queue

from config.endstop import EndstopConfig
from ..settings import Settings
from app.controllers.hardware.gpio import initialize_button, register_button_callback, remove_button_callback, is_button_pressed
from app.controllers.hardware.motors import get_motor_controller, MotorController


class Endstop:
    # Added 'loop' parameter
    def __init__(self, config: EndstopConfig, controller: MotorController):
        self.model = config
        self.settings = Settings(config, on_change=self._apply_settings_to_hardware)
        self._motor_controller = controller
        self._pin = config.pin
        self._event_queue = Queue()  # Create a queue for this instance
        self._listener_task = None  # To hold the reference to the listener task

        initialize_button(self._pin, pull_up=self.model.pull_up, bounce_time=self.model.bounce_time)
        register_button_callback(self._pin, "when_released", self._gpio_callback)
        self._motor_controller.endstop = self
        print(f"Endstop for motor {self.model.motor_name} initialized on pin {self._pin}.")


    def _apply_settings_to_hardware(self, config: EndstopConfig):
        remove_button_callback(self._pin, "when_released")
        self.model = config
        self._pin = config.pin
        initialize_button(self._pin, pull_up=self.model.pull_up, bounce_time=self.model.bounce_time)
        register_button_callback(self._pin, "when_released", self._gpio_callback)


    def get_config(self):
        return self.settings.model


    def get_status(self):
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
            print(f"Endstop: Motor is not busy. Moving back by {move_back_degrees} degrees. ---")
            await self._motor_controller.move_degrees(move_back_degrees)


    def _gpio_callback(self):
        """
        Immediate, synchronous callback executed by gpiozero in its thread.
        Only put a marker event into the queue, do not block or call async functions.
        """
        try:
             # Put a simple marker event into the queue
             self._event_queue.put_nowait("pressed")
             # Log that the raw event was detected (optional)
             # logger.debug(f"Endstop '{self.model.name}' raw press detected on pin {self._pin}. Event queued.")
        except asyncio.QueueFull:
             print(f"Endstop for motor '{self.model.motor_name}' event queue is full. Event dropped.")
        except Exception as e:
             print(f"Error in endstop GPIO callback for pin {self._pin}: {e}")


    async def _process_events(self):
        """
        ASYNCHRONOUS task running in the main event loop.
        Waits for events from the queue and handles them.
        """
        while True:
             try:
                  event = await self._event_queue.get()

                  if event == "pressed":
                       # 1. Stop the motor immediately (this should be thread-safe)
                       self._motor_controller.stop()
                       await asyncio.sleep(0.1)
                       self._motor_controller.model.angle = self.model.angular_position


                       # 2. Set motor position to the defined endstop angle
                       self._motor_controller.model.angle = self.model.angular_position
                       # Also reset internal motor state if necessary
                       self._motor_controller._target_angle = None # Clear target


                       # 3. Optionally move back slightly (asynchronously)
                       # Small delay before moving back, allows system to settle
                       await asyncio.sleep(0.1)
                       await self._motor_controller.move_degrees(-2)

                  # Mark the task as done
                  self._event_queue.task_done()

             except asyncio.CancelledError:
                  print("Event processor for Endstop cancelled.")
                  break # Exit loop if task is cancelled
             except Exception as e:
                  print(f"Error in event processor for Endstop: {e}")
                  # Avoid busy-looping on unexpected errors; wait a bit before retrying
                  await asyncio.sleep(1)


    def start_listener(self):
        """
        Starts the asynchronous event listener task.
        Should be called once from the main async context after initialization.
        """
        if self._listener_task is None or self._listener_task.done():
             print(f"Starting event listener for Endstop '{self.model.motor_name}'...")
             self._listener_task = asyncio.create_task(self._process_events())
             return self._listener_task
        else:
             print(f"Listener task for Endstop '{self.model.motor_name}' already running.")
             return self._listener_task


    def stop_listener(self):
        """
        Stops the asynchronous event listener task gracefully.
        """
        if self._listener_task and not self._listener_task.done():
             print(f"Stopping event listener for Endstop '{self.model.motor_name}'...")
             self._listener_task.cancel()
             # Optionally wait for the task to finish cancelling
             # await asyncio.wait([self._listener_task], timeout=1.0)
        else:
             print(f"Listener task for Endstop '{self.model.motor_name}' already stopped or not started.")
