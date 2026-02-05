## LinuxPy Camera Controller (Example)

This LinuxPy-based controller is an example for generic V4L2 devices (`/dev/video*`). 
It is meant as a starting point for community contributions and quick experiments,
not as a complete camera driver.

If you need extensive camera-specific behavior (custom controls, quirks, tuning,
workarounds), it is usually cleaner to create a dedicated camera controller for your device
(similar to `Picamera2Controller`) instead of growing the LinuxPy controller.

### What it does

- **Preview** uses a persistent `VideoCapture` session to avoid re-opening the
  device for each frame.
- **Photos** are captured as JPEG from the V4L2 stream.
- **Fallback formats** (RGB/YUV/pseudo-DNG) are derived from the JPEG stream.
- **EXIF orientation**: `CameraSettings.orientation_flag` is embedded into JPEG
  EXIF (same idea as Picamera2).

### Limitations

- Many devices don't expose controls via LinuxPy (no `set_control`,
  empty `controls`), so settings like saturation/contrast/gain/JPEG quality may
  not have any effect.
- No AF/AWB lock, no scaler cropping, no true RAW/DNG.

### Where to start tweaking

- `openscan_firmware/controllers/hardware/cameras/linuxpy.py`:
  - `_CONTROL_MAP` and `_JPEG_QUALITY_CONTROL` (control name mapping)
  - `_apply_basic_controls()` (where settings are applied)

### Verifying your webcam

```bash
v4l2-ctl --list-devices
python - <<'PY'
from linuxpy.video.device import Device
dev = Device("/dev/videoX")  # replace with your device node
dev.open()
print("has set_control:", hasattr(dev, "set_control"))
print("controls:", dev.controls)
dev.close()
PY
```
