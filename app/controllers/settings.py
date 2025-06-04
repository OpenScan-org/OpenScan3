"""
Settings module with callback-enabled attribute access for hardware components.

Provides direct attribute access to settings while allowing callbacks when settings change.
"""
import logging
from typing import Any, Callable, Generic, Optional, Tuple, TypeVar, get_type_hints

from pydantic import BaseModel

logger = logging.getLogger(__name__)

T = TypeVar('T', bound=BaseModel)

class Settings(Generic[T]):
    """
    A settings wrapper that provides direct attribute access with callback functionality.
    
    This class wraps a Pydantic BaseModel instance and provides attribute access to it.
    When settings are changed, a callback function is called.
    
    Example usage:
        # Create settings with a callback
        settings = Settings(CameraSettings(), on_change=apply_to_hardware)
        
        # Direct attribute access
        settings.shutter = 20000  # Automatically calls the callback
        
        # Batch update
        settings.update(shutter=20000, contrast=1.2)
    """
    
    def __init__(self, settings: T, on_change: Optional[Callable[[T], None]] = None):
        """
        Initialize settings with a callback.
        
        Args:
            settings: The initial settings object (must be a Pydantic BaseModel)
            on_change: Function to call when settings change
            settings_file: Optional JSON file to load/save settings
            settings_dir: Directory for settings files, default is "settings"
            autosave: If True, automatically save to file on every change of settings
        """
        self._settings = settings
        self._on_change = on_change
        logger.debug(f"Initialized '{settings.__class__.__name__}': {settings}")

    def __getattr__(self, name: str) -> Any:
        """Allow direct attribute access to the wrapped settings."""
        if name.startswith('_'):
            return super().__getattribute__(name)
        return getattr(self._settings, name)
        
    def __setattr__(self, name: str, value: Any) -> None:
        """Intercept attribute setting to trigger callback."""
        if name.startswith('_'):
            super().__setattr__(name, value)
            return

        logger.debug(f"Trying to set '{name}' to '{value}'")


        # Update the setting
        settings_dict = self._settings.model_dump()
        settings_dict[name] = value
        new_settings = self._settings.__class__(**settings_dict)
        logger.debug(f"Set '{name}' to '{value}'")

        
        # Set the new settings and trigger callback
        self._settings = new_settings
        if self._on_change:
            self._on_change(self._settings)
    
    def update(self, **kwargs) -> bool:
        """
        Update multiple settings at once.
        
        Args:
            **kwargs: Settings to update as keyword arguments
        """
        # Only update if we have valid arguments
        if not kwargs:
            return False

        logger.debug(f"Trying to update old settings: {self._settings} with new settings: {kwargs}")

        # Create updated settings object
        settings_dict = self._settings.model_dump()
        settings_dict.update({k: v for k, v in kwargs.items() if v is not None})
        new_settings = self._settings.__class__(**settings_dict)

        logger.debug(f"Updating settings: {kwargs}")

        # Set the new settings and trigger callback
        self._settings = new_settings
        if self._on_change:
            self._on_change(self._settings)

        return True
    
    def replace(self, new_settings: T) -> None:
        """
        Replace all settings at once.
        
        Args:
            new_settings: New settings object (must be the same type as initial settings)
        """
        logger.debug(f"Trying to replace old settings: {self._settings} with new settings: {new_settings}")
        if not isinstance(new_settings, self._settings.__class__):
            logger.error(f"Expected {self._settings.__class__.__name__}, got {type(new_settings).__name__}")
            raise TypeError(f"Expected {self._settings.__class__.__name__}, got {type(new_settings).__name__}")

        logger.debug(f"Replacing settings with: {new_settings}")
        self._settings = new_settings
        if self._on_change:
            self._on_change(self._settings)

    @property
    def model(self) -> T:
        """Get the underlying settings model."""
        return self._settings