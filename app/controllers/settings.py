from typing import TypeVar, Generic, Dict, Tuple, Any, Optional, Callable, get_type_hints

T = TypeVar('T')


class SettingsManager(Generic[T]):
    """
    Generic settings manager for both hardware and service components
    """

    def __init__(self, model: T, on_settings_changed: Optional[Callable[[T], None]] = None):
        self.model = model
        if not hasattr(model, 'settings') or model.settings is None:
            raise ValueError(f"Model {model} has no settings attribute or settings are None")
        self._settings = model.settings
        self._settings_callback = on_settings_changed

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

    def replace_settings(self, settings: T) -> None:
        """Update all settings at once"""
        self._settings = settings
        self._execute_settings_callback()

    def _execute_settings_callback(self) -> None:
        """
        Execute the callback function if one was provided.
        """
        if self._settings_callback:
            self._settings_callback(self._settings)

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