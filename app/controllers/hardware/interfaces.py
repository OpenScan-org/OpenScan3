from typing import Protocol, TypeVar, runtime_checkable, Dict, Generic
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

class ControllerFactory(Generic[T, M]):
    """
    Generic Controller Factory für Hardware und Software Controller
    
    T: Controller-Typ (z.B. CameraController, MotorController)
    M: Model-Typ (z.B. Camera, Motor)
    """
    _controllers: Dict[str, T] = {}
    
    @classmethod
    def get_controller(cls, model: M) -> T:
        """Get or create a controller instance for the given model"""
        if model.name not in cls._controllers:
            cls._controllers[model.name] = cls._create_controller(model)
        return cls._controllers[model.name]
            
    @classmethod
    def get_all_controllers(cls) -> Dict[str, T]:
        """Get a copy of all active controllers"""
        return cls._controllers.copy()
    
    @classmethod
    def _create_controller(cls, model: M) -> T:
        """Create a new controller instance. Override this if special creation logic is needed."""
        return cls._controller_class(model)