# Picamera2 autofocus tuning

OpenScan selects `AfRangeMacro` for autofocus. For the measured OpenScan Mini
with an IMX519, it adjusts `rpi.af.ranges.macro` to `11.0–15.0` dioptres with a
default of `12.0` before creating `Picamera2`.

The controller uses `imx519.json` for `imx519` and `arducam_64mp.json` for
`arducam_64mp`. Picamera2 searches its standard libcamera tuning directories.
The vendor files are not modified on disk.

Other scanner and camera combinations currently use the vendor tuning range
unchanged.

For preview and photo autofocus, OpenScan selects:

```python
"AfRange": controls.AfRangeEnum.Macro
```

`AfWindows` remains independent and controls the image area used for focus
measurement. If the tuning file cannot be loaded, OpenScan logs a warning and
uses the libcamera defaults.
