from typing import Protocol, TypeVar, runtime_checkable
from abc import abstractmethod

T = TypeVar('T')


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