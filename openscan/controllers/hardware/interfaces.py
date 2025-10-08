from typing import Protocol, runtime_checkable, Dict, Callable, Tuple, TypeVar
from abc import abstractmethod

T = TypeVar('T')
M = TypeVar('M')

@runtime_checkable
class HardwareInterface(Protocol[T]):
    """Base interface for all hardware components"""

    @abstractmethod
    def get_status(self) -> dict:
        """Get current hardware status"""
        ...

    @abstractmethod
    def get_config(self) -> T:
        """Get current configuration"""
        ...


@runtime_checkable
class StatefulHardware(HardwareInterface[T], Protocol[T]):
    """Interface for hardware that can be busy (motors, cameras)"""

    @abstractmethod
    def is_busy(self) -> bool:
        """
        Check if hardware is currently busy
        Returns:
            bool: True if hardware is busy
        """
        ...


@runtime_checkable
class SwitchableHardware(HardwareInterface[T], Protocol[T]):
    """Interface for hardware that can be switched on/off (lights or arbitrary devices connected to gpio pins)"""

    @abstractmethod
    def is_on(self) -> bool:
        """Check if hardware is turned on"""
        ...

@runtime_checkable
class EventHardware(HardwareInterface[T], Protocol[T]):
    """Interface for hardware that generates events (buttons, sensors, etc.)"""

    @abstractmethod
    def has_event(self) -> bool:
        """
        Check if hardware has a pending event
        Returns:
            bool: True if there is an event
        """
        ...


"""
Utility functions for creating hardware controller registries.
"""

C = TypeVar('C')  # C for controller type
M = TypeVar('M')  # M for model, like motor, camera, etc.


def create_controller_registry(controller_class: Callable[[M], C]) -> Tuple[
    Callable[[M], C],  # create_controller
    Callable[[str], C],  # get_controller
    Callable[[str], bool],  # remove_controller
    Dict[str, C]  # registry
]:
    """
    Create a generic registry for any type of hardware controller.
    Works with any controller class that accepts a model in its constructor.

    Args:
        controller_class: The controller class to create instances of

    Returns:
        Tuple of functions and registry for managing controllers
    """
    registry: Dict[str, C] = {}

    def create_controller(model: M) -> C:
        """Create or get a controller for the given hardware model"""
        if model.name not in registry:
            registry[model.name] = controller_class(model)
        return registry[model.name]

    def get_controller(name: str):
        """Get a controller by its name"""
        if name not in registry:
            raise ValueError(f"Controller not found: {name}")
        return registry[name]

    def remove_controller(name: str):
        """Remove a controller from the registry"""
        if name in registry:
            # call cleanup function (if it exists)
            controller = registry[name]
            if hasattr(controller, "cleanup") and callable(controller.cleanup):
                controller.cleanup()

            # remove from registry
            del registry[name]
            return True
        return False

    return create_controller, get_controller, remove_controller, registry