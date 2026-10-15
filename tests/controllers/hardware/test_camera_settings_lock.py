"""Verify settings serialization and camera lock nesting without camera hardware."""

import threading
from concurrent.futures import Future, TimeoutError

import pytest

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
