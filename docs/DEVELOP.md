# Development

For a detailed architectural overview and task system design, see:
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- [`docs/TASKS.md`](docs/TASKS.md)
- [`docs/WEBSOCKETS.md`](docs/WEBSOCKETS.md)

### Setup

#### Desktop/Linux

```sh
git clone https://github.com/OpenScan-org/OpenScan3.git
cd OpenScan3
python3 -m venv .venv
source .venv/bin/activate

# Install the package in editable mode with dev extras (runtime + tooling)
pip install -e .[dev]
```

Run the API in dev mode (auto-reload):

```sh
python -m openscan_firmware serve --reload --host 0.0.0.0 --port 8000
```

Run tests (skip hardware/camera-specific tests on non‑Pi):

```sh
pytest -q -k "not picamera2"
```

Note: On non‑Pi systems, camera features may be unavailable; skip camera tests as shown above.


### Raspberry Pi (hardware development)

For camera/hardware development directly on a Raspberry Pi OS (Lite) system:

```sh
sudo apt-get update && sudo apt-get install -y \
  git libgphoto2-dev libcap-dev python3-dev python3-libcamera python3-kms++ python3-opencv

# Optional (per camera): install PiVariety/libcamera packages as per vendor docs
# See Arducam IMX519 docs:
# https://docs.arducam.com/Raspberry-Pi-Camera/Native-camera/16MP-IMX519/

git clone https://github.com/OpenScan-org/OpenScan3.git
cd OpenScan3
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install -e .[dev]  # Drop [dev] if you only need runtime dependencies

# Start the API with auto-reload
python -m openscan_firmware serve --reload --host 0.0.0.0 --port 8000
```

### First Steps after Setup

You need to load a device configuration specific to your hardware setup. By default, no specific configuration is loaded.

There are three ways to load a configuration:

**Method 1: Using the SPA client (Recommended)**

1.  If you booted from the official OpenScan image, the bundled SPA client is available at `http://openscan3-alpha`.
2.  Open the page in a browser on the same network; the guided setup wizard walks you through selecting the correct hardware profile.
3.  Confirm the suggested configuration; the SPA will push it to the firmware and trigger any required reloads automatically.

**Method 2: Using the API docs**

1.  Navigate to the API documentation at `http://openscan3-alpha:8000/latest/docs`.
2.  Find the **Device** Section and the **PUT** endpoint `/latest/device/configurations/current`.
3.  Use the "Try it out" feature.
4.  In the **Request body**, enter the name of the configuration file you want to load. For example, for an OpenScan Mini with a Greenshield, use:
    ```json
    {
      "config_file": "default_mini_greenshield.json"
    }
    ```
    You can find available default configuration files in the local `settings/device/` folder of your checkout.
5.  Execute the request. If successful, you should receive a `200 OK` response, and the hardware corresponding to the configuration should initialize.

**Method 3: Manual File Copy**

Alternatively, copy a default settings file to `device_config.json` and restart the service:

```bash
cp settings/device/default_mini_greenshield.json device_config.json
sudo systemctl restart openscan-firmware.service
```

After loading the correct configuration, your OpenScan hardware should be ready to use via the web interface.


### Core task overrides (advanced)

- The firmware enforces a fixed list of required tasks (`scan_task`, `focus_stacking_task`, `cloud_upload_task`, `cloud_download_task`). Startup fails fast if any of those names are missing after autodiscovery.
- To ship a custom implementation, keep the same `task_name` and either place the module in one of the scanned namespaces or extend the namespace list via your own package (Autodiscovery scans `openscan_firmware.controllers.services.tasks` plus `openscan_firmware.tasks.community`).
- Enable autodiscovery by exporting `OPENSCAN_TASK_AUTODISCOVERY=1` (e.g., via `.env` or a systemd drop-in). If you need to replace an existing registration, also set `OPENSCAN_TASK_OVERRIDE_ON_CONFLICT=1`. Only flip this when you fully control the replacement task.
- Example tasks (e.g., `examples/crop_task.py`) remain opt-in; leave them untouched if you only need the production features.
-