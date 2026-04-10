from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from openscan_firmware.config.trigger import TriggerConfig
from openscan_firmware.controllers.hardware.triggers import create_trigger_controller, remove_trigger_controller
from openscan_firmware.controllers.hardware.triggers import TriggerExecution
from openscan_firmware.models.trigger import Trigger
from openscan_firmware.routers.next.triggers import router


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


@pytest_asyncio.fixture
async def trigger_client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.mark.asyncio
async def test_trigger_external_camera_returns_execution_payload(trigger_client: httpx.AsyncClient) -> None:
    execution = TriggerExecution(
        triggered_at=datetime(2026, 4, 8, 12, 0, 0),
        completed_at=datetime(2026, 4, 8, 12, 0, 1),
        duration_ms=1000,
    )
    controller = MagicMock()
    controller.trigger = AsyncMock(return_value=execution)

    with patch(
        "openscan_firmware.routers.next.triggers.get_trigger_controller",
        return_value=controller,
    ):
        response = await trigger_client.post(
            "/triggers/external-camera/trigger",
            json={
                "pre_trigger_delay_ms": 10,
                "post_trigger_delay_ms": 20,
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "external-camera"
    assert body["duration_ms"] == 1000


@pytest.mark.asyncio
async def test_patch_trigger_settings_updates_controller_settings(trigger_client: httpx.AsyncClient) -> None:
    with patch(
        "openscan_firmware.controllers.hardware.triggers.gpio.initialize_output_pins",
    ), patch(
        "openscan_firmware.controllers.hardware.triggers.gpio.set_output_pin",
    ), patch(
        "openscan_firmware.controllers.hardware.triggers.schedule_device_status_broadcast",
    ), patch(
        "openscan_firmware.controllers.hardware.triggers.notify_busy_change",
    ):
        controller = create_trigger_controller(
            Trigger(
                name="external-camera",
                settings=TriggerConfig(pin=23, active_level="active_high", pulse_width_ms=100),
            )
        )
        try:
            response = await trigger_client.patch(
                "/triggers/external-camera/settings",
                json={"pin": 24, "active_level": "active_low", "pulse_width_ms": 250},
            )
        finally:
            remove_trigger_controller("external-camera")

    assert response.status_code == 200
    body = response.json()
    assert body["pin"] == 24
    assert body["active_level"] == "active_low"
    assert body["pulse_width_ms"] == 250
    assert controller.settings.model.pin == 24
