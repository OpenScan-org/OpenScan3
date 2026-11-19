import importlib
import io
import sys
import types

import numpy as np
import piexif
from PIL import Image


def _write_jpeg_with_exif(path, pixel_value, model):
    pixels = np.full((48, 48, 3), pixel_value, dtype=np.uint8)
    Image.fromarray(pixels).save(path, format="JPEG")
    exif = {
        "0th": {
            piexif.ImageIFD.Make: "RaspberryPi",
            piexif.ImageIFD.Model: model,
            piexif.ImageIFD.Software: "OpenScan3 test",
        },
        "Exif": {
            piexif.ExifIFD.ExposureTime: (25_000, 1_000_000),
            piexif.ExifIFD.ISOSpeedRatings: 200,
        },
        "GPS": {},
        "1st": {},
        "thumbnail": None,
    }
    output = io.BytesIO()
    piexif.insert(piexif.dump(exif), path.read_bytes(), output)
    path.write_bytes(output.getvalue())


def test_focus_stack_preserves_exif_from_middle_focus_image(tmp_path, monkeypatch, request):
    # The stack algorithm is stubbed below so this metadata test can run without
    # loading OpenCV's native extension; image reads/writes still use real JPEGs.
    cv2 = types.ModuleType("cv2")
    cv2.CV_32F = 5
    cv2.Laplacian = lambda image, depth, ksize: np.zeros_like(image)
    cv2.boxFilter = lambda image, depth, size: image
    cv2.resize = lambda image, size: np.zeros((size[1], size[0]), dtype=np.float32)
    monkeypatch.setitem(sys.modules, "cv2", cv2)
    sys.modules.pop("openscan_firmware.utils.photos.stacking", None)
    stacking = importlib.import_module("openscan_firmware.utils.photos.stacking")
    request.addfinalizer(lambda: sys.modules.pop("openscan_firmware.utils.photos.stacking", None))

    monkeypatch.setattr(
        stacking,
        "load_image",
        lambda path: np.asarray(Image.open(path).convert("RGB")),
    )
    monkeypatch.setattr(stacking, "resize_to_gray", lambda image, scale: np.zeros((24, 24), dtype=np.float32))
    monkeypatch.setattr(stacking, "apply_transform", lambda image, transform: image)
    monkeypatch.setattr(
        stacking,
        "compute_focus_map",
        lambda image, downscale: np.zeros(image.shape[:2], dtype=np.float32),
    )
    monkeypatch.setattr(
        stacking,
        "save_image",
        lambda path, image, quality: Image.fromarray(image).save(path, format="JPEG", quality=quality),
    )

    image_paths = [tmp_path / f"focus_{index}.jpg" for index in range(3)]
    for index, path in enumerate(image_paths):
        _write_jpeg_with_exif(path, 50 + index * 50, f"RP_test_{index}")

    output_path = tmp_path / "stacked.jpg"
    identity = np.eye(2, 3, dtype=np.float32)
    stacking.FocusStacker(downscale=0.5).stack(
        [str(path) for path in image_paths],
        str(output_path),
        transforms=[identity.copy() for _ in image_paths],
    )

    exif = piexif.load(str(output_path))
    assert exif["0th"][piexif.ImageIFD.Make] == b"RaspberryPi"
    assert exif["0th"][piexif.ImageIFD.Model] == b"RP_test_1"
    assert exif["0th"][piexif.ImageIFD.Software] == b"OpenScan3 test"
    assert exif["Exif"][piexif.ExifIFD.ExposureTime] == (25_000, 1_000_000)
    assert exif["Exif"][piexif.ExifIFD.ISOSpeedRatings] == 200
