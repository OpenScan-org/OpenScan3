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

    libcamera.ColorSpace = _ColorSpace
    libcamera.Transform = type("Transform", (), {})
    libcamera.controls = types.SimpleNamespace(
        AfMeteringEnum=_AfMeteringEnum,
        AfModeEnum=_AfModeEnum,
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
        {"AfMode": module.controls.AfModeEnum.Continuous},
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
        {"AfMode": module.controls.AfModeEnum.Auto},
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
