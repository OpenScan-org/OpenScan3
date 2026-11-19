import importlib
import io
import sys
import types
from types import SimpleNamespace

import piexif
import pytest
from PIL import Image

from openscan_firmware.config.camera import CameraSettings


def _import_picamera2_module(monkeypatch):
    libcamera = types.ModuleType("libcamera")

    class _ColorSpace:
        Sycc = "Sycc"

    class _AfModeEnum:
        Continuous = "continuous"
        Auto = "auto"
        Manual = "manual"

    class _AfMeteringEnum:
        Windows = "windows"

    class _AfRangeEnum:
        Macro = "macro"

    libcamera.ColorSpace = _ColorSpace
    libcamera.Transform = type("Transform", (), {})
    libcamera.controls = types.SimpleNamespace(
        AfModeEnum=_AfModeEnum,
        AfMeteringEnum=_AfMeteringEnum,
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


@pytest.mark.parametrize("capture_session", [False, True])
def test_capture_jpeg_writes_capture_metadata_to_exif(monkeypatch, request, capture_session):
    module = _import_picamera2_module(monkeypatch)
    request.addfinalizer(
        lambda: sys.modules.pop("openscan_firmware.controllers.hardware.cameras.picamera2", None)
    )
    controller = object.__new__(module.Picamera2Controller)
    controller.camera = SimpleNamespace(name="arducam_64mp")
    controller.settings = SimpleNamespace(
        AF=False,
        jpeg_quality=90,
        orientation_flag=1,
        model=CameraSettings(),
    )
    controller.photo_config = object()
    controller._capture_session_active = capture_session
    controller._capture_session_config = None
    controller._capture_session_image_format = "jpeg"
    controller._ensure_capture_session_mode = lambda config: None
    controller._configure_focus = lambda **kwargs: None
    controller._configure_cropping_for_scalercrop = lambda: None
    controller._set_busy = lambda busy: None

    class FakePicamera:
        options = {}

        def switch_mode_and_capture_file(self, config, destination, *, format, exif_data):
            assert format == "jpeg"
            return self._write_jpeg(destination, exif_data)

        def capture_file(self, destination, *, format, exif_data, wait):
            assert format == "jpeg"
            assert wait is True
            return self._write_jpeg(destination, exif_data)

        @staticmethod
        def _write_jpeg(destination, exif_data):
            jpeg_buffer = io.BytesIO()
            Image.new("RGB", (8, 8), color=(32, 64, 96)).save(jpeg_buffer, format="JPEG")
            exif = piexif.load(jpeg_buffer.getvalue())
            exif["0th"].update(exif_data["0th"])
            piexif.insert(piexif.dump(exif), jpeg_buffer.getvalue(), destination)
            return {
                "ExposureTime": 25_000,
                "AnalogueGain": 1.0,
                "DigitalGain": 2.0,
                "LensPosition": 12.0,
            }

    controller._picam = FakePicamera()
    controller._create_artifact = lambda data, image_format, metadata: SimpleNamespace(
        data=data,
        format=image_format,
        camera_metadata=SimpleNamespace(raw_metadata=metadata),
    )

    artifact = controller.capture_jpeg()
    exif = piexif.load(artifact.data.getvalue())

    assert artifact.format == "jpeg"
    assert exif["0th"][piexif.ImageIFD.Make] == b"RaspberryPi"
    assert exif["0th"][piexif.ImageIFD.Model] == b"RP_arducam-64mp"
    assert exif["0th"][piexif.ImageIFD.Orientation] == 1
    assert exif["Exif"][piexif.ExifIFD.ExposureTime] == (25_000, 1_000_000)
    assert exif["Exif"][piexif.ExifIFD.ISOSpeedRatings] == 200
    assert artifact.camera_metadata.raw_metadata["LensPosition"] == 12.0
