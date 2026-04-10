from unittest.mock import MagicMock, patch

import pytest

from openscan_firmware.config.trigger import TriggerConfig
from openscan_firmware.controllers.hardware.triggers import TriggerController
from openscan_firmware.models.trigger import Trigger


@pytest.mark.asyncio
async def test_trigger_controller_toggles_pin_and_returns_execution() -> None:
    initialize_output_pins = MagicMock()
    set_output_pin = MagicMock()

    with patch(
        "openscan_firmware.controllers.hardware.triggers.gpio.initialize_output_pins",
        initialize_output_pins,
    ), patch(
        "openscan_firmware.controllers.hardware.triggers.gpio.set_output_pin",
        set_output_pin,
    ), patch(
        "openscan_firmware.controllers.hardware.triggers.schedule_device_status_broadcast",
    ), patch(
        "openscan_firmware.controllers.hardware.triggers.notify_busy_change",
    ):
        controller = TriggerController(
            Trigger(
                name="External Camera",
                settings=TriggerConfig(
                    pin=23,
                    active_level="active_high",
                    pulse_width_ms=1,
                ),
            )
        )
        execution = await controller.trigger(pre_trigger_delay_ms=0, post_trigger_delay_ms=0)
        await controller.reset()

    assert execution.duration_ms >= 0
    assert execution.completed_at >= execution.triggered_at
    assert initialize_output_pins.call_count == 2
    assert set_output_pin.call_count == 4


def test_trigger_controller_settings_update_reapplies_idle_level() -> None:
    initialize_output_pins = MagicMock()
    set_output_pin = MagicMock()

    with patch(
        "openscan_firmware.controllers.hardware.triggers.gpio.initialize_output_pins",
        initialize_output_pins,
    ), patch(
        "openscan_firmware.controllers.hardware.triggers.gpio.set_output_pin",
        set_output_pin,
    ), patch(
        "openscan_firmware.controllers.hardware.triggers.schedule_device_status_broadcast",
    ), patch(
        "openscan_firmware.controllers.hardware.triggers.notify_busy_change",
    ):
        controller = TriggerController(
            Trigger(
                name="External Camera",
                settings=TriggerConfig(
                    pin=23,
                    active_level="active_high",
                    pulse_width_ms=10,
                ),
            )
        )

        controller.settings.update(pin=24, active_level="active_low", pulse_width_ms=25)

    assert controller.settings.model.pin == 24
    assert controller.settings.model.active_level == "active_low"
    assert controller.settings.model.pulse_width_ms == 25
    assert initialize_output_pins.call_args_list[-1].args == ([24],)
    assert set_output_pin.call_args_list[-1].args == (24, True)
