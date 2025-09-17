import time

from picamera2.utils import orientation_to_transform

from app.controllers.hardware.cameras.camera import create_camera_controller
from app.config.camera import CameraSettings
from app.models.camera import Camera, CameraType, PhotoData


camera_settings = CameraSettings(
    crop_width=20,
    crop_height=20,
    orientation_flag=6,
    shutter=50000,
)

camera_name = "arducam_64mp" #  or "imx519"

camera = Camera(
            type=CameraType.PICAMERA2,
            name=camera_name,
            path="/dev/video0",
            settings=camera_settings
        )

camera_controller = create_camera_controller(camera)

print(camera_controller.calibrate_awb_and_lock())

def test_capture_jpg():
    start = time.time()
    artifact = camera_controller.capture_jpeg()
    print(f"Captured jpg in {time.time() - start} seconds.")

    start = time.time()
    with open("test.jpg", "wb") as f:
        f.write(artifact.data.getvalue())
    print(f"Saved jpg in {time.time() - start} seconds.")
    print(artifact.camera_metadata)

    assert artifact.data is not None
    assert artifact.camera_metadata is not None

def test_capture_dng():
    start = time.time()
    artifact = camera_controller.capture_dng()
    print(f"Captured dng in {time.time() - start} seconds.")

    start = time.time()
    with open("test.dng", "wb") as f:
        f.write(artifact.data.getvalue())
    print(f"Saved dng in {time.time() - start} seconds.")
    print(artifact.camera_metadata)

    assert artifact.data is not None
    assert artifact.camera_metadata is not None

def test_capture_rgb_array():
    start = time.time()
    artifact = camera_controller.capture_rgb_array()
    print(f"Captured rgb array in {time.time() - start} seconds.")
    print(f"Shape of the captured array: {artifact.data.shape}")
    print(artifact.camera_metadata)

    assert artifact.data is not None
    assert artifact.camera_metadata is not None

def test_capture_yuv_array():
    start = time.time()
    artifact = camera_controller.capture_yuv_array()
    print(f"Captured yuv array in {time.time() - start} seconds.")
    print(f"Shape of the captured array: {artifact.data.shape}")
    print(artifact.camera_metadata)

    assert artifact.data is not None
    assert artifact.camera_metadata is not None