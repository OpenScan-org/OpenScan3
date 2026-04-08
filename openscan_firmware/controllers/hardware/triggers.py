from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime

from openscan_firmware.config.trigger import TriggerActiveLevel, TriggerConfig
from openscan_firmware.controllers.hardware import gpio
from openscan_firmware.controllers.hardware.interfaces import TriggerableHardware, create_controller_registry
from openscan_firmware.controllers.settings import Settings
from openscan_firmware.controllers.services.device_events import notify_busy_change, schedule_device_status_broadcast
from openscan_firmware.models.trigger import Trigger


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TriggerExecution:
    triggered_at: datetime
    completed_at: datetime
    duration_ms: int


class TriggerController(TriggerableHardware[TriggerConfig]):
    """GPIO-backed trigger controller with persistent device-level settings."""

    def __init__(self, trigger: Trigger):
        self.model = trigger
        self.settings = Settings(trigger.settings, on_change=self._apply_settings_to_hardware)
        self._busy = False
        self._last_execution: TriggerExecution | None = None
        self._apply_settings_to_hardware(self.settings.model)

    def _resolve_logic_levels(self, settings: TriggerConfig) -> tuple[bool, bool]:
        active_state = settings.active_level == TriggerActiveLevel.ACTIVE_HIGH
        inactive_state = not active_state
        return active_state, inactive_state

    def _apply_settings_to_hardware(self, settings: TriggerConfig) -> None:
        self.model.settings = settings
        _, inactive_state = self._resolve_logic_levels(settings)
        gpio.initialize_output_pins([settings.pin])
        gpio.set_output_pin(settings.pin, inactive_state)
        schedule_device_status_broadcast([f"triggers.{self.model.name}.settings"])

    def get_status(self) -> dict:
        return {
            "name": self.model.name,
            "busy": self._busy,
            "settings": self.get_config().model_dump(),
            "last_triggered_at": self._last_execution.triggered_at if self._last_execution else None,
            "last_completed_at": self._last_execution.completed_at if self._last_execution else None,
            "last_duration_ms": self._last_execution.duration_ms if self._last_execution else None,
        }

    def get_config(self) -> TriggerConfig:
        return self.settings.model

    def is_busy(self) -> bool:
        return self._busy

    def _set_busy(self, busy: bool) -> None:
        if self._busy == busy:
            return
        self._busy = busy
        notify_busy_change("triggers", self.model.name)

    async def trigger(
        self,
        pre_trigger_delay_ms: int = 0,
        post_trigger_delay_ms: int = 0,
    ) -> TriggerExecution:
        settings = self.settings.model
        if not settings.enabled:
            raise RuntimeError(f"Trigger '{self.model.name}' is disabled.")
        if self._busy:
            raise RuntimeError(f"Trigger '{self.model.name}' is already busy.")

        active_state, inactive_state = self._resolve_logic_levels(settings)
        self._set_busy(True)
        try:
            if pre_trigger_delay_ms:
                await asyncio.sleep(pre_trigger_delay_ms / 1000)

            triggered_at = datetime.now()
            gpio.set_output_pin(settings.pin, active_state)
            await asyncio.sleep(settings.pulse_width_ms / 1000)
            gpio.set_output_pin(settings.pin, inactive_state)

            if post_trigger_delay_ms:
                await asyncio.sleep(post_trigger_delay_ms / 1000)

            completed_at = datetime.now()
            execution = TriggerExecution(
                triggered_at=triggered_at,
                completed_at=completed_at,
                duration_ms=max(0, int((completed_at - triggered_at).total_seconds() * 1000)),
            )
            self._last_execution = execution
            schedule_device_status_broadcast([f"triggers.{self.model.name}"])
            return execution
        finally:
            self._set_busy(False)

    async def reset(self) -> None:
        settings = self.settings.model
        _, inactive_state = self._resolve_logic_levels(settings)
        gpio.initialize_output_pins([settings.pin])
        gpio.set_output_pin(settings.pin, inactive_state)

    def cleanup(self) -> None:
        try:
            settings = self.settings.model
            _, inactive_state = self._resolve_logic_levels(settings)
            gpio.initialize_output_pins([settings.pin])
            gpio.set_output_pin(settings.pin, inactive_state)
        except Exception as exc:  # pragma: no cover - defensive cleanup
            logger.warning("Failed to cleanup trigger '%s': %s", self.model.name, exc)


create_trigger_controller, get_trigger_controller, remove_trigger_controller, _trigger_registry = create_controller_registry(TriggerController)


def get_all_trigger_controllers():
    """Get all currently registered trigger controllers."""
    return _trigger_registry.copy()
