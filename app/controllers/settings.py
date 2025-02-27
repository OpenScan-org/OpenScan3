from typing import TypeVar, Generic, Dict, Tuple, Any, Optional, Callable, get_type_hints, Union
from pathlib import Path
import json

# Get the project root directory (where settings/ is located)
PROJECT_ROOT = Path(__file__).parent.parent.parent

T = TypeVar('T')


class SettingsManager(Generic[T]):
    """
    Generic settings manager for both hardware and service components.

    Handles loading, saving, and managing settings for components. Can optionally
    persist settings to JSON files and automatically save on changes.

    Args:
        model: The model instance that contains the settings attribute
        on_settings_changed: Optional callback when settings are changed
        settings_file: Optional JSON file to load/save settings
        settings_dir: Directory for settings files, default is "settings"
        autosave: If True, automatically save to file on every change of settings
    """

    def __init__(self, model: T, 
                 on_settings_changed: Optional[Callable[[T], None]] = None,
                 settings_file: Optional[str] = None,
                 settings_dir: str = "settings",
                 autosave: bool = False):
        self.model = model
        self._settings = model.settings
        self._settings_dir = PROJECT_ROOT / settings_dir
        self._settings_file = settings_file
        if not hasattr(model, 'settings') or model.settings is None:
            raise ValueError(f"Model {model} has no settings attribute or settings are None")

        # Use settings_file from model if available and none provided
        if settings_file is None and hasattr(model, 'settings_file'):
            settings_file = model.settings_file
            self.load_from_file()
        self._settings_file = settings_file

        self._settings_callback = on_settings_changed
        self._autosave = autosave


    def get_setting(self, setting: str) -> Any:
        """Get value of a specific setting"""
        if not hasattr(self._settings, setting):
            raise ValueError(f"Unknown setting: {setting}")
        return getattr(self._settings, setting)

    def set_setting(self, setting: str, value: Any) -> None:
        """Set value of a specific setting"""
        if not hasattr(self._settings, setting):
            raise ValueError(f"Unknown setting: {setting}")
        setattr(self._settings, setting, self.convert_value(setting, value))
        self._execute_settings_callback()

    def get_all_settings(self) ->  T:
        """Get all settings as a dictionary"""
        return self._settings

    def replace_settings(self, settings: T, execute_callback: bool = True) -> None:
        """Update all settings at once"""
        self._settings = settings
        if execute_callback:
            self._execute_settings_callback()

    def _execute_settings_callback(self) -> None:
        """Execute the callback function and handle autosave if enabled."""
        if self._settings_callback:
            self._settings_callback(self._settings)
        if self._autosave and self._settings_file:  # autosave, if activated
            self.save_to_file()

    def convert_value(self, setting: str, value: str):
        """Convert a string value to the correct type for the given setting"""
        type_hints = get_type_hints(self._settings)
        if setting not in type_hints:
            raise ValueError(f"Unknown setting: {setting}")

        target_type = type_hints[setting]

        if target_type == Optional[int]:
            return int(value)
        elif target_type == Optional[float]:
            return float(value)
#        elif target_type == Optional[bool]:
#            return value.lower() in {"true", "1", "yes"}
        elif target_type == Optional[Tuple[int, int]]:
            return tuple(map(int, value.split(",")))

        return value  # in case of str value

    def load_from_file(self) -> bool:
        """Load settings from JSON file if settings_file is configured"""
        print(f"Loading settings from {self._settings_file}")
        if not self._settings_file:
            return False
            
        file_path = self._settings_dir / self._settings_file
        if not file_path.exists():
            return False
            
        try:
            with open(file_path, 'r') as f:
                data = json.load(f)
                # Get the actual settings class from our current settings
                settings_class = self._settings.__class__
                new_settings = settings_class(**data)
                # Don't execute callback during initial load
                self.replace_settings(new_settings, execute_callback=False)
                print("Settings loaded from file")
                return True
        except Exception as e:
            print(f"Error loading settings: {e}")
            return False

    def save_to_file(self) -> bool:
        """Save current settings to JSON file if settings_file is configured"""
        if not self._settings_file:
            return False
            
        try:
            file_path = self._settings_dir / self._settings_file
            file_path.parent.mkdir(parents=True, exist_ok=True)
            
            with open(file_path, 'w') as f:
                # Use Pydantic's dict() method to serialize
                json.dump(self._settings.dict(), f, indent=2)
            print(f"Saved settings to {file_path}")
            return True
        except Exception as e:
            print(f"Error saving settings: {e}")
            return False

    @property
    def settings_file(self) -> Optional[str]:
        """Get current settings file name"""
        return self._settings_file

    @settings_file.setter
    def settings_file(self, filename: Optional[str]) -> None:
        """
        Set settings file name.
        
        Args:
            filename: New settings file name or None to disable file operations
        """
        self._settings_file = filename

    @property
    def autosave(self) -> bool:
        """Get current autosave setting"""
        return self._autosave

    @autosave.setter
    def autosave(self, value: bool) -> None:
        """Enable or disable automatic saving of settings to file on changes"""
        self._autosave = value

    def attach_file(self, filename: str, load_immediately: bool = True, save_current: bool = False) -> bool:
        """
        Attach a settings file and optionally handle existing settings
        
        Args:
            filename: Settings file to attach
            load_immediately: If True, load settings from file immediately
            save_current: If True and there's a current settings file, save current settings before switching
        
        Returns:
            bool: True if all operations were successful
        """
        if save_current and self._settings_file:
            if not self.save_to_file():
                return False
                
        self.settings_file = filename
        
        if load_immediately:
            return self.load_from_file()
        return True