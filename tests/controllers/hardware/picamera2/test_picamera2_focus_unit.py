import importlib
import sys
import types

from openscan_firmware.config.camera import CameraSettings


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


def test_configure_focus_uses_default_af_window_when_none_is_set(monkeypatch):
    module = _import_picamera2_module(monkeypatch)
    controller = object.__new__(module.Picamera2Controller)
    controller.settings = CameraSettings(AF=True, AF_window=None)
    controller._picam = _FakePicam()

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


def test_configure_cropping_preserves_still_controls_and_leaves_analysis_uncropped(monkeypatch):
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
