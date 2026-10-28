import importlib
import errno
import sys
import types
from unittest.mock import Mock

import pytest

from openscan_firmware.config.camera import CameraSettings
from openscan_firmware.controllers.hardware.cameras import camera as camera_module
from openscan_firmware.models.camera import Camera, CameraType


def _import_picamera2_module(monkeypatch):
    libcamera = types.ModuleType("libcamera")

    class _ColorSpace:
        Sycc = "Sycc"

    class _AfMeteringEnum:
        Windows = "windows"

    class _AfModeEnum:
        Continuous = "continuous"
        Auto = "auto"
        Manual = "manual"

    class _AfRangeEnum:
        Macro = "macro"

    libcamera.ColorSpace = _ColorSpace
    libcamera.Transform = type("Transform", (), {})
    libcamera.controls = types.SimpleNamespace(
        AfMeteringEnum=_AfMeteringEnum,
        AfModeEnum=_AfModeEnum,
        AfRangeEnum=_AfRangeEnum,
    )

    picamera2 = types.ModuleType("picamera2")
    picamera2.Picamera2 = type("Picamera2", (), {})
    cv2 = types.ModuleType("cv2")

    monkeypatch.setitem(sys.modules, "libcamera", libcamera)
    monkeypatch.setitem(sys.modules, "picamera2", picamera2)
    monkeypatch.setitem(sys.modules, "cv2", cv2)
    sys.modules.pop("openscan_firmware.controllers.hardware.cameras.picamera2", None)

    return importlib.import_module("openscan_firmware.controllers.hardware.cameras.picamera2")


class _FakePicam:
    def __init__(self, lens_position=1.0):
        self.camera_properties = {"PixelArraySize": (200, 100)}
        self.controls = []
        self._lens_position = lens_position

    def set_controls(self, values):
        self.controls.append(values)
        if "LensPosition" in values:
            self._lens_position = values["LensPosition"]

    def capture_metadata(self):
        return {"LensPosition": self._lens_position}


class _FakeRequest:
    def make_array(self, _name):
        return "array"

    def get_metadata(self):
        return {"SensorTimestamp": 1}

    def release(self):
        pass


class _SessionFakePicam(_FakePicam):
    def __init__(self):
        super().__init__()
        self.switch_mode_calls = []
        self.capture_request_calls = 0

    def switch_mode(self, config, wait=True):
        self.switch_mode_calls.append((config, wait))
        return True

    def capture_request(self, wait=True):
        self.capture_request_calls += 1
        return _FakeRequest()


def test_configure_focus_sets_preview_autofocus_window(monkeypatch):
    module = _import_picamera2_module(monkeypatch)
    controller = object.__new__(module.Picamera2Controller)
    controller.settings = CameraSettings(AF=True, AF_window=(10, 20, 30, 40))
    controller._picam = _FakePicam()

    controller._configure_focus(camera_mode="preview")

    assert controller._picam.controls == [
        {
            "AfMetering": module.controls.AfMeteringEnum.Windows,
            "AfWindows": [(20, 60, 40, 30)],
        },
        {
            "AfMode": module.controls.AfModeEnum.Continuous,
            "AfRange": module.controls.AfRangeEnum.Macro,
        },
    ]


@pytest.mark.parametrize("camera_properties", [
    {"PixelArraySize": (200, 100)},
    {"PixelArrayReadableArea": (48, 40, 200, 100)},
    {"PixelArraySize": (200, 100), "PixelArrayReadableArea": (48, 40, 300, 150)},
], ids=["legacy", "readable-area", "legacy-preferred"])
def test_configure_focus_uses_default_af_window_when_none_is_set(monkeypatch, camera_properties):
    module = _import_picamera2_module(monkeypatch)
    controller = object.__new__(module.Picamera2Controller)
    controller.settings = CameraSettings(AF=True, AF_window=None)
    controller._picam = _FakePicam()
    controller._picam.camera_properties = camera_properties

    controller._configure_focus(camera_mode="photo")

    assert controller._picam.controls == [
        {
            "AfMetering": module.controls.AfMeteringEnum.Windows,
            "AfWindows": [(90, 45, 20, 10)],
        },
        {
            "AfMode": module.controls.AfModeEnum.Auto,
            "AfRange": module.controls.AfRangeEnum.Macro,
        },
    ]


def test_configure_focus_sets_default_manual_focus(monkeypatch):
    module = _import_picamera2_module(monkeypatch)
    controller = object.__new__(module.Picamera2Controller)
    controller.settings = CameraSettings(AF=False, manual_focus=None)
    controller._picam = _FakePicam(lens_position=0.0)

    controller._configure_focus()

    assert controller.settings.manual_focus == 1.0
    assert controller._picam.controls == [
        {"AfMode": module.controls.AfModeEnum.Manual, "LensPosition": 1.0}
    ]


def test_manual_camera_controls_are_shared_by_preview_and_still_capture(monkeypatch):
    module = _import_picamera2_module(monkeypatch)
    settings = CameraSettings(
        shutter=500.0,
        gain=2.0,
        saturation=1.2,
        contrast=1.3,
        awbg_red=1.4,
        awbg_blue=1.5,
    )

    controls = module.Picamera2Controller._manual_camera_controls(settings)

    assert controls == {
        "ExposureTime": 500000,
        "AnalogueGain": 2.0,
        "Saturation": 1.2,
        "Contrast": 1.3,
        "ColourGains": (1.4, 1.5),
    }


def test_capture_session_captures_without_switching_back(monkeypatch):
    module = _import_picamera2_module(monkeypatch)
    controller = object.__new__(module.Picamera2Controller)
    controller.settings = CameraSettings(AF=False, manual_focus=1.0)
    controller.camera = types.SimpleNamespace(name="test_camera")
    controller._picam = _SessionFakePicam()
    controller._busy = False
    controller._restart_error = None
    controller._capture_session_active = True
    controller._capture_session_config = None
    controller._capture_session_image_format = "rgb_array"
    controller.rgb_config = {"name": "rgb"}

    array, metadata = controller._capture_array(controller.rgb_config)

    assert array == "array"
    assert metadata == {"SensorTimestamp": 1}
    assert controller._picam.capture_request_calls == 1
    assert controller._picam.switch_mode_calls == [(controller.rgb_config, True)]


@pytest.mark.parametrize("camera_properties", [
    {"PixelArraySize": (200, 100)},
    {"PixelArrayReadableArea": (48, 40, 200, 100)},
], ids=["legacy", "readable-area"])
def test_configure_cropping_preserves_still_controls_and_leaves_analysis_uncropped(monkeypatch, camera_properties):
    module = _import_picamera2_module(monkeypatch)

    class _FakeStrategy:
        def __init__(self):
            self.preview_controls = None
            self.photo_controls = None
            self.raw_controls = None
            self.yuv_controls = None
            self.rgb_controls = None

        def create_preview_config(self, _picam, _resolution, controls):
            self.preview_controls = controls
            return {"preview": controls}

        def create_photo_config(self, _picam, _resolution, controls):
            self.photo_controls = controls
            return {"photo": controls}

        def create_raw_config(self, _picam, _resolution, controls):
            self.raw_controls = controls
            return {"raw": controls}

        def create_yuv_config(self, _picam, controls):
            self.yuv_controls = controls
            return {"yuv": controls}

        def create_rgb_config(self, _picam, controls):
            self.rgb_controls = controls
            return {"rgb": controls}

    strategy = _FakeStrategy()
    controller = object.__new__(module.Picamera2Controller)
    controller.settings = CameraSettings(
        crop_width=10,
        crop_height=20,
        orientation_flag=1,
        shutter=500.0,
        gain=2.0,
        saturation=1.2,
        contrast=1.3,
        awbg_red=1.4,
        awbg_blue=1.5,
    )
    controller.camera = types.SimpleNamespace(settings=controller.settings)
    controller._picam = _FakePicam()
    controller._picam.camera_properties = camera_properties
    controller._strategy = strategy
    controller._photogrammetry_settings = {
        "AeEnable": False,
        "NoiseReductionMode": 0,
        "AwbEnable": False,
    }

    crop = controller._configure_cropping_for_scalercrop()

    assert crop == (10, 10, 180, 80)
    expected_still_controls = {
        "AeEnable": False,
        "NoiseReductionMode": 0,
        "AwbEnable": False,
        "ExposureTime": 500000,
        "AnalogueGain": 2.0,
        "Saturation": 1.2,
        "Contrast": 1.3,
        "ColourGains": (1.4, 1.5),
        "ScalerCrop": crop,
    }

    assert strategy.preview_controls == {
        "AeEnable": False,
        "NoiseReductionMode": 0,
        "AwbEnable": False,
    }
    assert strategy.photo_controls == {
        **expected_still_controls,
    }
    assert strategy.raw_controls == strategy.photo_controls
    assert strategy.yuv_controls == {
        key: value for key, value in expected_still_controls.items() if key != "ScalerCrop"
    }
    assert strategy.rgb_controls == strategy.yuv_controls


@pytest.fixture
def recovery_camera(monkeypatch):
    module = _import_picamera2_module(monkeypatch)
    controller = object.__new__(module.Picamera2Controller)
    monkeypatch.setattr(camera_module, "notify_busy_change", lambda *args: None)
    monkeypatch.setattr(camera_module, "schedule_device_status_broadcast", lambda *args: None)
    camera_module.CameraController.__init__(controller, Camera(
        name="test_camera", type=CameraType.PICAMERA2, path="mock",
        settings=CameraSettings(AF=False, manual_focus=1),
    ))
    controller._picam = Mock()
    controller._create_picamera = Mock(return_value=Mock())
    controller._configure_focus = Mock()
    controller._configure_resolutions = Mock()
    controller._apply_settings_to_hardware = Mock()
    controller._capture_session_active = False
    controller._capture_session_config = None
    controller._capture_session_image_format = None
    controller.rgb_config = {"name": "rgb"}
    controller.yuv_config = {"name": "yuv"}
    controller.preview_config = {"name": "preview"}
    return controller


@pytest.mark.parametrize("error", [
    OSError(errno.ENOMEM, "Cannot allocate memory"),
    RuntimeError("Failed to start camera: Cannot allocate memory"),
])
@pytest.mark.parametrize("image_format", ["rgb_array", "yuv_array"])
@pytest.mark.parametrize("session", [False, True])
def test_array_oom_propagates_once_without_focus_cleanup(recovery_camera, error, image_format, session, caplog):
    camera = recovery_camera
    camera._capture_session_active = session
    camera._capture_session_config = {"active": True} if session else None
    capture = camera._picam.capture_request if session else camera._picam.switch_mode_and_capture_request
    capture.side_effect = error

    with pytest.raises(type(error)) as caught:
        camera.photo(image_format)

    assert caught.value is error
    assert capture.call_count == 1
    camera._configure_focus.assert_called_once_with(camera_mode="photo")
    camera._picam.switch_mode.assert_not_called()
    assert camera.is_busy() is False
    assert camera.get_status()["restart_required"] is True
    assert camera.get_status()["last_error"] == str(error)
    assert any(record.exc_info and record.exc_info[1] is error for record in caplog.records)


@pytest.mark.parametrize("error", [OSError(errno.EIO, "I/O failure"), RuntimeError("Unrelated capture error")])
def test_array_other_errors_are_not_retried_or_classified_as_oom(recovery_camera, error):
    camera = recovery_camera
    camera._picam.switch_mode_and_capture_request.side_effect = error

    with pytest.raises(type(error)) as caught:
        camera._capture_array(camera.rgb_config)

    assert caught.value is error
    assert camera._picam.switch_mode_and_capture_request.call_count == 1
    assert camera.restart_required is False
    assert camera.is_busy() is False
    camera._configure_focus.assert_called_once_with(camera_mode="photo")


@pytest.mark.parametrize("fail", [False, True])
def test_array_releases_request_even_if_processing_fails(recovery_camera, fail):
    camera = recovery_camera
    request = camera._picam.switch_mode_and_capture_request.return_value
    request.make_array.return_value = "array"
    request.get_metadata.return_value = {"frame": 1}
    if fail:
        error = OSError(errno.ENOMEM, "Cannot allocate memory")
        request.make_array.side_effect = error
        with pytest.raises(OSError) as caught:
            camera._capture_array(camera.rgb_config)
        assert caught.value is error
        assert camera.restart_required is True
    else:
        assert camera._capture_array(camera.rgb_config) == ("array", {"frame": 1})
        assert camera._configure_focus.call_args.kwargs == {"camera_mode": "preview"}
    request.release.assert_called_once_with()


@pytest.mark.asyncio
async def test_oom_skips_capture_session_hardware_cleanup(recovery_camera):
    camera = recovery_camera
    error = OSError(errno.ENOMEM, "Cannot allocate memory")
    camera._picam.capture_request.side_effect = error

    with pytest.raises(OSError) as caught:
        async with camera.capture_session("rgb_array"):
            camera._capture_session_config = {"active": True}
            await camera.photo_async("rgb_array")

    assert caught.value is error
    camera._picam.switch_mode.assert_not_called()
    camera._configure_focus.assert_called_once_with(camera_mode="photo")
    assert camera._capture_session_active is False
    assert camera._capture_session_config is None
    assert camera.restart_required is True


def test_successful_restart_clears_oom_only_after_configuration(recovery_camera, monkeypatch):
    camera = recovery_camera
    camera._record_camera_error(OSError(errno.ENOMEM, "Cannot allocate memory"))
    old_picam = camera._picam

    def configure_focus(**kwargs):
        assert camera.restart_required is True

    camera._configure_focus.side_effect = configure_focus
    camera.restart_camera()

    old_picam.cancel_all_and_flush.assert_called_once_with()
    old_picam.close.assert_called_once_with()
    camera._picam.start.assert_called_once_with()
    camera._apply_settings_to_hardware.assert_called_once_with(camera.camera.settings)
    assert camera.get_status()["restart_required"] is False
    assert camera.get_status()["last_error"] is None
    capture = Mock(return_value="photo")
    monkeypatch.setattr(camera, "capture_jpeg", capture)
    assert camera.photo() == "photo"
    capture.assert_called_once_with()


@pytest.mark.parametrize("stage", ["close", "start", "settings", "focus"])
def test_failed_restart_preserves_oom_state(recovery_camera, stage):
    camera = recovery_camera
    error = OSError(errno.ENOMEM, "Cannot allocate memory")
    camera._record_camera_error(error)
    failing_step = {
        "close": camera._picam.close,
        "start": camera._create_picamera.return_value.start,
        "settings": camera._apply_settings_to_hardware,
        "focus": camera._configure_focus,
    }[stage]
    failing_step.side_effect = RuntimeError("Restart failed")

    with pytest.raises(RuntimeError, match="Restart failed"):
        camera.restart_camera()

    assert camera.restart_required is True
    assert camera.get_status()["last_error"] == str(error)
