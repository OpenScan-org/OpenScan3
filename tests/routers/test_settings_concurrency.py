"""Exercise settings HTTP requests with real models and controlled worker interleavings."""

import asyncio
import threading
from importlib import import_module
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import APIRouter, FastAPI

from openscan_firmware.config.camera import CameraSettings
from openscan_firmware.controllers.settings import Settings


@pytest_asyncio.fixture(params=["v0_8", "v0_9", "next"])
async def settings_api(request):
    module = import_module(f"openscan_firmware.routers.{request.param}.settings_utils")
    controller = SimpleNamespace(settings=Settings(CameraSettings()))
    router = APIRouter(prefix="/cameras")
    module.create_settings_endpoints(router, "camera", lambda name: controller, CameraSettings)
    app = FastAPI()
    app.include_router(router)

    @app.get("/health")
    async def health():
        return {"ok": True}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client, controller


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["PUT", "PATCH"])
async def test_waiting_settings_callback_keeps_api_responsive(settings_api, method):
    client, controller = settings_api
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    release = threading.Event()
    callback_threads = []

    def apply_to_hardware(settings):
        callback_threads.append(threading.get_ident())
        loop.call_soon_threadsafe(entered.set)
        if not release.wait(timeout=5):
            raise RuntimeError("Test did not release settings callback")

    controller.settings._on_change = apply_to_hardware
    pending = asyncio.create_task(client.request(method, "/cameras/cam0/settings", json={"gain": 2}))
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        assert not pending.done(), "A write must await its hardware callback"
        health = await asyncio.wait_for(client.get("/health"), timeout=1)
        assert health.status_code == 200
        assert len(callback_threads) == 1
        assert callback_threads[0] != threading.get_ident()
    finally:
        release.set()
        response = await asyncio.wait_for(pending, timeout=2)

    assert response.status_code == 200
    assert response.json()["gain"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["PUT", "PATCH"])
async def test_settings_callback_error_returns_422_without_rollback(settings_api, method):
    client, controller = settings_api

    def fail_to_apply(settings):
        raise RuntimeError("Camera is unavailable")

    controller.settings._on_change = fail_to_apply
    response = await client.request(method, "/cameras/cam0/settings", json={"gain": 2})

    assert response.status_code == 422
    assert response.json()["detail"] == "Camera is unavailable"
    # Existing behavior: model assignment precedes the hardware callback.
    assert controller.settings.gain == 2
    assert (await client.get("/health")).status_code == 200

    controller.settings._on_change = None
    retry = await client.patch("/cameras/cam0/settings", json={"gain": 3})
    assert retry.status_code == 200
    assert retry.json()["gain"] == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["PUT", "PATCH"])
async def test_invalid_settings_do_not_reach_hardware_or_replace_model(settings_api, method):
    client, controller = settings_api
    original_model = controller.settings.model
    applied = []
    controller.settings._on_change = applied.append

    response = await client.request(method, "/cameras/cam0/settings", json={"gain": -1})

    assert response.status_code == 422
    assert controller.settings.model is original_model
    assert applied == []


@pytest.mark.asyncio
async def test_awaited_patches_preserve_other_fields_and_apply_in_order(settings_api):
    client, controller = settings_api
    applied = []
    controller.settings._on_change = lambda settings: applied.append((settings.shutter, settings.gain))

    for payload in ({"shutter": 100}, {"gain": 2}, {"shutter": 200}):
        response = await client.patch("/cameras/cam0/settings", json=payload)
        assert response.status_code == 200

    assert applied == [(100, 1), (100, 2), (200, 2)]
    response = await client.get("/cameras/cam0/settings")
    assert response.json()["shutter"] == 200
    assert response.json()["gain"] == 2


@pytest.mark.asyncio
async def test_concurrent_patches_preserve_both_fields(settings_api, monkeypatch):
    client, controller = settings_api
    initial_model = controller.settings.model
    original_dump = CameraSettings.model_dump
    both_read = threading.Barrier(2, timeout=2)

    def controlled_dump(model, *args, **kwargs):
        snapshot = original_dump(model, *args, **kwargs)
        if model is initial_model:
            # Let both workers read the old model before either writes it back.
            # If writes are serialized in a future fix, the first worker proceeds
            # after the barrier timeout and the second reads the updated model.
            try:
                both_read.wait()
            except threading.BrokenBarrierError:
                pass
        return snapshot

    monkeypatch.setattr(CameraSettings, "model_dump", controlled_dump)
    first, second = await asyncio.wait_for(
        asyncio.gather(
            client.patch("/cameras/cam0/settings", json={"shutter": 100}),
            client.patch("/cameras/cam0/settings", json={"contrast": 2}),
        ),
        timeout=6,
    )

    assert first.status_code == second.status_code == 200
    result = (await client.get("/cameras/cam0/settings")).json()
    assert (result["shutter"], result["contrast"]) == (100, 2), "A successful PATCH was discarded"
