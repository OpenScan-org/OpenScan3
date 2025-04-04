from gpiozero import DigitalOutputDevice, Button
from typing import Dict, List, Optional, Callable


# Track pins and buttons
_output_pins = {}
_buttons = {}


def initialize_output_pins(pins: List[int]):
    """Initializes one or more GPIO pins as digital outputs."""
    for pin in pins:
        if pin in _output_pins:
            print(f"Warning: Output pin {pin} already initialized.")
        elif pin in _buttons:
            print(f"Error: Cannot initialize pin {pin} as output. Already initialized as Button.")
        else:
            try:
                _output_pins[pin] = DigitalOutputDevice(pin, initial_value=False)
                print(f"Initialized pin {pin} as DigitalOutputDevice.")
            except Exception as e:
                print(f"Error initializing output pin {pin}: {e}")
                # Clean up if initialization failed partially
                if pin in _output_pins:
                    del _output_pins[pin]


def toggle_output_pin(pin: int):
    """Toggles the state of an output pin."""
    if pin in _output_pins:
        _output_pins[pin].toggle()
    else:
        print(f"Warning: Cannot toggle pin {pin}. Not initialized as output.")


def set_output_pin(pin: int, status: bool):
    """Sets the state of an output pin."""
    if pin in _output_pins:
        _output_pins[pin].value = status
    else:
        print(f"Warning: Cannot set pin {pin}. Not initialized as output.")


def get_initialized_pins() -> Dict[str, List[int]]:
    """Returns a dictionary listing initialized output pins and buttons."""
    return {
        "output_pins": list(_output_pins.keys()),
        "buttons": list(_buttons.keys())
    }


def get_output_pin(pin: int):
    """Returns the state of an output pin."""
    if pin in _output_pins:
        return _output_pins[pin].value
    else:
        print(f"Warning: Pin {pin} not initialized as output.")
        return None


def initialize_button(pin: int, pull_up: Optional[bool] = True, bounce_time: Optional[float] = 0.05):
    """
    Initializes a GPIO pin as button input using gpiozero.Button.

    Args:
        pin: GPIO pin number to initialize as button.
        pull_up: If True (default), use internal pull-up resistor (button connects pin to GND).
                 If False, use internal pull-down resistor (button connects pin to 3.3V).
                 Set to None to disable internal resistors (requires external resistor).
        bounce_time: Debounce time in seconds (default: 0.05s or 50ms).
    """
    if pin in _buttons:
        print(f"Warning: Button on pin {pin} already initialized.")
    elif pin in _output_pins:
        print(f"Error: Cannot initialize pin {pin} as Button. Already initialized as output.")
    else:
        try:
            _buttons[pin] = Button(pin, pull_up=pull_up, bounce_time=bounce_time)
            print(f"Initialized pin {pin} as Button (pull_up={pull_up}, bounce_time={bounce_time})")
        except Exception as e:
            print(f"Error initializing button on pin {pin}: {e}")
            # Clean up if initialization failed partially
            if pin in _buttons:
                del _buttons[pin]


def register_button_callback(pin: int, event_type: str, callback: Callable[[], None]):
    """
    Registers a callback function for a button event ('pressed' or 'released').
    The pin must be initialized as a button first using initialize_buttons.

    Args:
        pin: The GPIO pin number of the button.
        event_type: The event to listen for ('pressed' or 'released').
        callback: The function to call when the event occurs (takes no arguments).
    """
    if pin not in _buttons:
        print(f"Error: Button on pin {pin} not initialized. Call initialize_buttons() first.")
        return

    button_obj = _buttons[pin]
    if event_type == 'pressed':
        button_obj.when_pressed = callback
        print(f"Registered 'when_pressed' callback for button on pin {pin}.")
    elif event_type == 'released':
        button_obj.when_released = callback
        print(f"Registered 'when_released' callback for button on pin {pin}.")
    else:
        print(f"Error: Invalid event_type '{event_type}'. Use 'pressed' or 'released'.")


def remove_button_callback(pin: int, event_type: str):
    """
    Removes a previously registered callback function for a button event.

    Args:
        pin: The GPIO pin number of the button.
        event_type: The event type ('pressed' or 'released') whose callback should be removed.
    """
    if pin not in _buttons:
        print(f"Warning: Cannot remove callback. Button on pin {pin} not initialized.")
        return

    button_obj = _buttons[pin]
    if event_type == 'pressed':
        if button_obj.when_pressed is None:
             print(f"Warning: No 'when_pressed' callback was registered for button on pin {pin}.")
        else:
            button_obj.when_pressed = None
            print(f"Removed 'when_pressed' callback for button on pin {pin}.")
    elif event_type == 'released':
        if button_obj.when_released is None:
             print(f"Warning: No 'when_released' callback was registered for button on pin {pin}.")
        else:
            button_obj.when_released = None
            print(f"Removed 'when_released' callback for button on pin {pin}.")
    else:
        print(f"Error: Invalid event_type '{event_type}'. Use 'pressed' or 'released'.")


def is_button_pressed(pin: int) -> Optional[bool]:
     """
     Checks if the button on the specified pin is currently pressed.
     The pin must be initialized as a button first.

     Args:
         pin: The GPIO pin number.

     Returns:
         True if the button is currently pressed, False if released.
         None if the pin is not initialized as a button.
     """
     if pin in _buttons:
         return _buttons[pin].is_pressed
     else:
         # No warning here, as this might be called speculativey.
         # Returning None indicates it's not a known button.
         return None
