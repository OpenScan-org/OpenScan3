# Hardware Compatibility

## Reference setup

The primary development combinations are:

| Raspberry Pi | Camera |
| --- | --- |
| Raspberry Pi 4, 2 GB RAM | Arducam IMX519 |
| Raspberry Pi 4, 2 GB RAM | Arducam Hawkeye |

Before a new OpenScan3 release, real tests are made with this setup.

## Other Raspberry Pi Models

**Raspberry Pi 3**. Reportedly works with Arducam IMX519.

**Raspberry Pi 5**. Reportedly works with Arducam IMX519, use CAM0 slot.


## Other Cameras

| Camera                             | Status | Notes                                                                                                                                                       |
|------------------------------------| --- |-------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Manual camera trigger via GPIO     | **Out of the box** | Trigger arbitrary cameras offering shutter control. See [Camera via External Trigger](Camera/EXTERNAL_TRIGGER.md).                                          |
| GPhoto2 cameras                    | **Fine tuning required** | Camera-specific profiles and settings may need to be added or adjusted. See [Add a GPhoto2 Camera Profile](Camera/GPHOTO2_ADD_CAMERA.md).                   |
| LinuxPy/V4L2 cameras (e.g. Webcam) | **Fine tuning required** | Generic capture is available, but camera controls, autofocus, cropping, and RAW support vary by device. See [LinuxPy Camera Controller](Camera/LINUXPY.md). |
| Raspberry Pi Camera Module 3       | **Experimental** | Starts and takes pictures. Requires validation of the Picamera2/libcamera configuration and camera behavior.                                                |


Status definitions:

- **Out of the box**: included in the supported OpenScan3 hardware path and
  usable without firmware changes.
- **Fine tuning required**: the generic integration exists, but camera-specific
  profiles, controls, or configuration are likely needed.
- **Experimental**: an integration or platform path exists, and some features work, but the complete
  setup still needs testing on the target hardware.

