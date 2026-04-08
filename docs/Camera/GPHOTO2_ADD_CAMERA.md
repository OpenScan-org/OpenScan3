# Add a GPhoto2 Camera Profile

This guide shows how to add support for a new gphoto2-compatible camera using a
Python profile class.

## 1. Detect your camera

Connect camera over USB and run:

```bash
gphoto2 --auto-detect
```

Copy the detected model string. You will use parts of this string in
`_MODEL_MARKERS`.

## 2. Inspect config keys and choices

List available keys:

```bash
gphoto2 --list-config
```

Inspect important keys:

```bash
gphoto2 --get-config /main/settings/capturetarget
gphoto2 --get-config /main/capturesettings/shutterspeed
gphoto2 --get-config /main/imgsettings/imageformat
gphoto2 --get-config /main/imgsettings/iso
```

## 3. Copy the template profile

Copy:

`openscan_firmware/controllers/hardware/cameras/gphoto2/profiles/template_camera.py`

Create a new file with your camera name, for example:

`openscan_firmware/controllers/hardware/cameras/gphoto2/profiles/my_camera.py`

Then update:

- `profile_id`
- `_MODEL_MARKERS`
- config key lists (`_SHUTTER_KEYS`, `_ISO_KEYS`, `_RAW_FORMAT_KEYS`, ...)
- startup defaults in `apply_startup_config`
- optional RAW behavior in `capture_dng`

## 4. Register the profile

Add your class to:

`openscan_firmware/controllers/hardware/cameras/gphoto2/profiles/__init__.py`

Then add it in selection order in:

`openscan_firmware/controllers/hardware/cameras/gphoto2/profile_registry.py`

Place model-specific profiles before `GenericGPhoto2Profile`.

## 5. Run a JPEG test

Use the firmware API/flow to capture a JPEG and check:

- image is captured successfully
- expected shutter and quality values are applied
- diagnostics show expected config keys

## 6. Run a RAW test

Capture RAW/DNG via the firmware flow and verify:

- file extension is RAW-like for your camera (`.nef`, `.cr2`, `.raw`, ...)
- profile can switch to RAW mode
- profile restores previous image format after capture

## 7. Debug setting failures

`write_first_config(...)` returns explicit result details:

- attempted keys
- requested value
- success/failure
- failure message

If a setting fails, inspect these values first, then compare with
`gphoto2 --get-config <key>` output.
