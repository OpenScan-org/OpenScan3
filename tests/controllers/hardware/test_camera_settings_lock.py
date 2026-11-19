"""Verify settings serialization and camera lock nesting without camera hardware."""

import asyncio
import errno
import threading
from concurrent.futures import Future, TimeoutError
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from openscan_firmware.config.camera import CameraSettings
from openscan_firmware.controllers.hardware.cameras import camera as camera_module
from openscan_firmware.controllers.settings import Settings
from openscan_firmware.models.camera import Camera, CameraType


def _in_thread(function):
    """Bound deadlock checks without leaving an executor that hangs pytest on exit."""
    result = Future()

    def run():
        try:
            result.set_result(function())
        except BaseException as exc:
            result.set_exception(exc)

    threading.Thread(target=run, daemon=True).start()
    return result


def _change_contrast(settings, method):
    if method == "update":
        settings.update(contrast=2)
    elif method == "replace":
        settings.replace(CameraSettings(contrast=2))
    else:
        settings.contrast = 2


class _CameraWithoutHardware(camera_module.CameraController):
    def __init__(self):
        super().__init__(Camera(name="cam0", type=CameraType.PICAMERA2, path="mock", settings=CameraSettings()))
        self.applied = []

    def _apply_settings_to_hardware(self, settings):
        self.applied.append(settings)

    def preview(self):
        return b"preview"

    def capture_jpeg(self):
        return b"photo"

    capture_dng = capture_jpeg
    capture_rgb_array = capture_jpeg
    capture_yuv_array = capture_jpeg


@pytest.fixture
def camera(monkeypatch):
    monkeypatch.setattr(camera_module, "schedule_device_status_broadcast", lambda *args: None)
    monkeypatch.setattr(camera_module, "notify_busy_change", lambda *args: None)
    return _CameraWithoutHardware()


@pytest.mark.parametrize("method", ["update", "replace", "assignment"])
def test_settings_write_waits_for_previous_callback(method):
    entered = threading.Event()
    release = threading.Event()
    applied = []

    def apply(settings):
        applied.append(settings.contrast)
        if len(applied) == 1:
            entered.set()
            if not release.wait(timeout=5):
                raise RuntimeError("Test did not release settings callback")

    settings = Settings(CameraSettings(), on_change=apply)
    first = _in_thread(lambda: settings.update(shutter=100))
    second = None
    try:
        assert entered.wait(timeout=2)
        started = threading.Event()

        def write():
            started.set()
            _change_contrast(settings, method)

        second = _in_thread(write)
        assert started.wait(timeout=2)
        with pytest.raises(TimeoutError):
            second.result(timeout=0.1)
        assert settings.contrast == 1
        assert applied == [1]
    finally:
        release.set()
        first.result(timeout=2)
        if second is not None:
            second.result(timeout=2)

    assert settings.contrast == 2
    assert applied == [1, 2]


@pytest.mark.parametrize("method", ["update", "replace", "assignment"])
def test_camera_settings_wait_for_photo_before_changing_model(camera, monkeypatch, method):
    entered = threading.Event()
    release = threading.Event()
    original_model = camera.settings.model
    capture_models = []

    def capture():
        capture_models.append(camera.settings.model)
        entered.set()
        if not release.wait(timeout=5):
            raise RuntimeError("Test did not release photo capture")
        capture_models.append(camera.settings.model)
        return b"photo"

    monkeypatch.setattr(camera, "capture_jpeg", capture)
    photo = _in_thread(camera.photo)
    write = None
    try:
        assert entered.wait(timeout=2)
        started = threading.Event()

        def change_settings():
            started.set()
            _change_contrast(camera.settings, method)

        write = _in_thread(change_settings)
        assert started.wait(timeout=2)
        with pytest.raises(TimeoutError):
            write.result(timeout=0.1)
        assert camera.settings.model is original_model
        assert camera.camera.settings is original_model
        assert camera.applied == []
    finally:
        release.set()
        assert photo.result(timeout=2) == b"photo"
        if write is not None:
            write.result(timeout=2)

    assert all(model is original_model for model in capture_models)
    assert camera.settings.contrast == camera.camera.settings.contrast == 2
    assert camera.applied == [camera.settings.model]


def test_photo_can_change_settings_while_holding_hardware_lock(camera, monkeypatch):
    def capture():
        # Capture/focus setup may supply a missing focus setting inside photo().
        camera.settings.manual_focus = 1
        return b"photo"

    monkeypatch.setattr(camera, "capture_jpeg", capture)

    assert _in_thread(camera.photo).result(timeout=2) == b"photo"
    assert camera.settings.manual_focus == camera.camera.settings.manual_focus == 1
    assert camera.applied == [camera.settings.model]


def test_settings_callback_can_supply_a_missing_setting():
    applied = []

    def apply(model):
        applied.append(model.manual_focus)
        if model.manual_focus is None:
            settings.manual_focus = 1

    settings = Settings(CameraSettings(manual_focus=None), on_change=apply)

    _in_thread(lambda: settings.update(AF=False)).result(timeout=2)

    assert settings.AF is False
    assert settings.manual_focus == 1
    assert applied == [None, 1]


@pytest_asyncio.fixture(params=["v0_8", "v0_9", "next"])
async def snapshot_api(request, camera, monkeypatch):
    module = import_module(f"openscan_firmware.routers.{request.param}.cameras")
    monkeypatch.setattr(module, "get_camera_controller", lambda name: camera)
    app = FastAPI()
    app.include_router(module.router)

    @app.get("/health")
    async def health():
        return camera.get_status()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client, camera


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["photo", "settings"])
async def test_snapshot_waits_for_camera_and_keeps_api_responsive(snapshot_api, monkeypatch, operation):
    client, camera = snapshot_api
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    snapshot_started = asyncio.Event()
    release = threading.Event()
    finished = threading.Event()
    preview_called = threading.Event()
    snapshot_threads = []

    def blocking_hardware(*args):
        camera._set_busy(True)
        loop.call_soon_threadsafe(entered.set)
        try:
            if not release.wait(timeout=5):
                raise RuntimeError("Test did not release camera operation")
            finished.set()
            return b"photo"
        finally:
            camera._set_busy(False)

    def preview():
        assert finished.is_set(), "Snapshot must not overlap with another camera operation"
        preview_called.set()
        return b"snapshot-jpeg"

    original_snapshot = camera.preview_snapshot

    def snapshot():
        snapshot_threads.append(threading.get_ident())
        loop.call_soon_threadsafe(snapshot_started.set)
        return original_snapshot()

    monkeypatch.setattr(camera, "preview", preview)
    monkeypatch.setattr(camera, "preview_snapshot", snapshot)
    if operation == "photo":
        monkeypatch.setattr(camera, "capture_jpeg", blocking_hardware)
        worker = _in_thread(camera.photo)
    else:
        monkeypatch.setattr(camera, "_apply_settings_to_hardware", blocking_hardware)
        worker = _in_thread(lambda: camera.settings.update(contrast=2))

    pending = None
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        # Streams still skip a frame instead of waiting for the occupied lock.
        assert await asyncio.wait_for(camera.preview_async(), timeout=1) is None
        pending = asyncio.create_task(client.get("/cameras/cam0/preview", params={"mode": "snapshot"}))
        await asyncio.wait_for(snapshot_started.wait(), timeout=2)
        response = await asyncio.wait_for(client.get("/health"), timeout=1)
        assert response.status_code == 200
        assert response.json()["busy"] is True
        assert not pending.done()
        assert not preview_called.is_set()
        assert len(snapshot_threads) == 1
        assert snapshot_threads[0] != threading.get_ident()
    finally:
        release.set()
        worker.result(timeout=2)
        if pending is not None:
            response = await asyncio.wait_for(pending, timeout=2)

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content == b"snapshot-jpeg"
    assert preview_called.is_set()


@pytest.mark.asyncio
async def test_snapshot_error_returns_503_and_releases_camera_lock(snapshot_api, monkeypatch):
    client, camera = snapshot_api

    def fail():
        raise RuntimeError("Camera is unavailable")

    monkeypatch.setattr(camera, "preview", fail)
    response = await client.get("/cameras/cam0/preview", params={"mode": "snapshot"})

    assert response.status_code == 503
    assert response.json()["detail"] == "Camera is unavailable"
    # A different thread must be able to take the lock after the snapshot failed.
    assert _in_thread(camera.photo).result(timeout=2) == b"photo"

    monkeypatch.setattr(camera, "preview", lambda: b"snapshot-jpeg")
    response = await client.get("/cameras/cam0/preview", params={"mode": "snapshot"})
    assert response.status_code == 200
    assert response.content == b"snapshot-jpeg"


def test_photo_waits_until_snapshot_capture_finishes(camera, monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    photo_called = threading.Event()

    def preview():
        entered.set()
        if not release.wait(timeout=5):
            raise RuntimeError("Test did not release snapshot capture")
        return b"snapshot-jpeg"

    def capture():
        photo_called.set()
        return b"photo"

    monkeypatch.setattr(camera, "preview", preview)
    monkeypatch.setattr(camera, "capture_jpeg", capture)
    snapshot = _in_thread(camera.preview_snapshot)
    photo = None
    try:
        assert entered.wait(timeout=2)
        photo = _in_thread(camera.photo)
        with pytest.raises(TimeoutError):
            photo.result(timeout=0.1)
        assert not photo_called.is_set()
    finally:
        release.set()
        assert snapshot.result(timeout=2) == b"snapshot-jpeg"
        if photo is not None:
            assert photo.result(timeout=2) == b"photo"


@pytest.mark.asyncio
@pytest.mark.parametrize("version", ["v0_8", "v0_9", "next"])
@pytest.mark.parametrize("error", [
    OSError(errno.ENOMEM, "Cannot allocate memory"),
    RuntimeError("Failed to start camera: Cannot allocate memory"),
])
async def test_oom_is_visible_in_device_status_and_blocks_further_camera_calls(camera, monkeypatch, version, error):
    from openscan_firmware.controllers import device
    from openscan_firmware.controllers.services.device_events import DeviceEventPublisher

    cameras_router = import_module(f"openscan_firmware.routers.{version}.cameras")
    device_router = import_module(f"openscan_firmware.routers.{version}.device")
    monkeypatch.setitem(camera_module._camera_registry, "cam0", camera)
    monkeypatch.setattr(cameras_router, "get_camera_controller", lambda name: camera)
    monkeypatch.setattr(device, "get_all_camera_controllers", lambda: {"cam0": camera})
    for getter in ("get_all_motor_controllers", "get_all_light_controllers", "get_all_trigger_controllers"):
        monkeypatch.setattr(device, getter, lambda: {})
    monkeypatch.setattr(device, "_scanner_device", SimpleNamespace(
        name="Test scanner", model="mini", shield="greenshield", idle_timeout=0,
        motors_timeout=0, scan_radius_mm=1, startup_mode="startup_enabled",
        calibrate_mode="calibrate_manual", _idle=False, _initialized=True,
    ))

    def fail_capture():
        camera._set_busy(True)
        raise error

    capture = Mock(side_effect=fail_capture)
    preview = Mock(side_effect=AssertionError("Must not access camera after OOM"))
    monkeypatch.setattr(camera, "capture_jpeg", capture)
    monkeypatch.setattr(camera, "preview", preview)
    app = FastAPI()
    app.include_router(cameras_router.router)
    app.include_router(device_router.router)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get("/cameras/cam0/photo")
        expected_status = 503 if isinstance(error, RuntimeError) and version != "v0_8" else 500
        assert response.status_code == expected_status
        assert str(error) in response.text

        camera_response = await client.get("/cameras/cam0")
        device_response = await client.get("/device/info")
        assert camera_response.status_code == device_response.status_code == 200
        status = camera_response.json()
        assert status["busy"] is False
        assert status["restart_required"] is True
        assert status["last_error"] == str(error)
        assert device_response.json()["cameras"]["cam0"] == status

        photo_response = await client.get("/cameras/cam0/photo")
        preview_response = await client.get("/cameras/cam0/preview", params={"mode": "snapshot"})
        settings_response = await client.patch("/cameras/cam0/settings", json={"contrast": 2})
        assert photo_response.status_code == (500 if version == "v0_8" else 503)
        assert preview_response.status_code == 503
        assert settings_response.status_code == 422
        for response in (photo_response, preview_response, settings_response):
            assert "Camera restart required" in response.text

    capture.assert_called_once_with()
    preview.assert_not_called()
    assert camera.applied == []
    assert camera.get_status()["last_error"] == str(error)

    hub = SimpleNamespace(broadcast_json=AsyncMock())
    publisher = DeviceEventPublisher(hub_getter=lambda: hub, status_provider=device.get_device_info)
    await publisher.publish_status()
    namespace, event = hub.broadcast_json.call_args.args
    assert namespace == "device"
    assert event["device"]["cameras"]["cam0"]["restart_required"] is True
    assert event["device"]["cameras"]["cam0"]["last_error"] == str(error)


@pytest.mark.parametrize("error", [OSError(errno.EIO, "I/O failure"), RuntimeError("Unrelated camera error")])
def test_non_memory_errors_do_not_request_restart(camera, monkeypatch, error):
    capture = Mock(side_effect=error)
    monkeypatch.setattr(camera, "capture_jpeg", capture)

    with pytest.raises(type(error)) as caught:
        camera.photo()

    assert caught.value is error
    assert camera.get_status()["restart_required"] is False
    assert camera.get_status()["last_error"] is None


def test_status_broadcast_failure_does_not_hide_camera_oom(camera, monkeypatch):
    error = OSError(errno.ENOMEM, "Cannot allocate memory")
    monkeypatch.setattr(camera, "capture_jpeg", Mock(side_effect=error))
    monkeypatch.setattr(camera_module, "schedule_device_status_broadcast", Mock(side_effect=RuntimeError("WebSocket failed")))

    with pytest.raises(OSError) as caught:
        camera.photo()

    assert caught.value is error
    assert camera.get_status()["restart_required"] is True


@pytest.mark.parametrize("operation", ["preview_snapshot", "preview_stream", "settings"])
@pytest.mark.parametrize("error", [
    OSError(errno.ENOMEM, "Cannot allocate memory"),
    RuntimeError("Failed to start camera: Cannot allocate memory"),
])
def test_preview_and_settings_record_oom(camera, monkeypatch, operation, error):
    hardware_call = Mock(side_effect=error)
    if operation == "settings":
        monkeypatch.setattr(camera, "_apply_settings_to_hardware", hardware_call)
        invoke = lambda: camera.settings.update(contrast=2)
    else:
        monkeypatch.setattr(camera, "preview", hardware_call)
        invoke = camera.preview_snapshot if operation == "preview_snapshot" else camera._preview_non_blocking

    with pytest.raises(type(error)) as caught:
        invoke()
    assert caught.value is error
    assert camera.get_status()["restart_required"] is True
    assert camera.get_status()["last_error"] == str(error)

    with pytest.raises(RuntimeError, match="Camera restart required"):
        invoke()
    assert hardware_call.call_count == 1
