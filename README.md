<div id="top"></div>

# OpenScan3

<!-- ABOUT THE PROJECT -->
## About The Project

OpenScan3 is a firmware for controlling OpenScan devices, a family of OpenSource and OpenHardware devices designed to make photogrammetry accessible to everyone.

The goal of OpenScan3 is providing a hackable and extensible firmware for OpenScan devices and starting point for individual photogrammetry rigs.

OpenScan3 is under development and is not ready for production!

<p align="right">(<a href="#top">back to top</a>)</p>

<!-- GETTING STARTED -->
## Getting Started

There are two ways to get started on a Raspberry Pi: flash a ready-made image or build a custom one.

## Install OpenScan Image (Recommended)

Download the image from here: https://openscan.eu/pages/resources-downloads

Choose the image according to your camera variant:

- Generic (PiCamera)
- Arducam IMX519
- Arducam Hawkeye

Warning: Choosing the wrong image may result in permanent damage to your camera.

You can also use [OpenScan3 Pi Image Builder](https://github.com/esto-openscan/OpenScan3-pi-gen) to build a customized image:

- Clone the repository with submodules: `git clone --recurse-submodules https://github.com/esto-openscan/OpenScan3-pi-gen.git`
- Pick a camera configuration (for example `./build-all.sh generic` for the generic variant)
- Flash the generated image from `pi-gen/deploy/` with Raspberry Pi Imager or a similar tool

Refer to the [user guide](https://github.com/esto-openscan/OpenScan3-pi-gen/blob/main/DOCUMENTATION.md) in that repository for detailed build and usage instructions, including networking, services, and troubleshooting.

#### Accessing OpenScan3

*   **Default Hostname:** `openscan3-alpha` (or `openscan3-alpha.local` if mDNS is enabled)
*   **UI (Node-RED):** http://openscan3-alpha/ (Node-RED admin interface is available at http://openscan3-alpha/nodered)
*   **API documentation:** http://openscan3-alpha:8000/latest/docs (list versions at http://openscan3-alpha:8000/versions).




## Development

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
openscan --reload --host 0.0.0.0 --port 8000
# or: python -m openscan serve --reload --host 0.0.0.0 --port 8000
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
openscan --reload --host 0.0.0.0 --port 8000
```

### First Steps after Setup

You need to load a device configuration specific to your hardware setup. By default, no specific configuration is loaded.

There are two ways to load a configuration:

**Method 1: Using the API (Recommended)**

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

**Method 2: Manual File Copy**

Alternatively, copy a default settings file to `device_config.json` and restart the service:

```bash
cp settings/device/default_mini_greenshield.json device_config.json
sudo systemctl restart openscan3.service
```

After loading the correct configuration, your OpenScan hardware should be ready to use via the web interface.




<p align="right">(<a href="#top">back to top</a>)</p>


<!-- ROADMAP -->
## Roadmap

### Beta (January 2026)
- [x] WebSockets for tasks, device state, and scan progress
- [ ] OS/device services: Samba, USB, disk monitoring, stats; camera-assisted Wi‑Fi/setup
- [ ] Reliability: improved handling for Arducam Hawkeye 64MP memory issues
- [ ] Frontend improvements ([OpenScan3-client](https://github.com/OpenScan-org/OpenScan3-client))


### Release (May 2026)
- Turntable Mode as a ScanTask
- Enhanced hardware support
  - grblHAL
  - More Hardware controllers: displays, fans, buttons
  - Camera & capture: DSLR focus motor; broader camera support (PiCamera, DSLR via gphoto2, smartphones, external GPIO)
- Project export: Metashape, RealityCapture, 3DF Zephyr, Meshroom
- Automation: rsync-based project sync; new task features (auto-config via photo, background removal, drop detection)

### Future
- Further extend hardware support and hackability to use as base for photogrammetry rigs

For details and up-to-date status, see GitHub issues and check out the Discord channel.

<p align="right">(<a href="#top">back to top</a>)</p>



<!-- CONTRIBUTING -->
## Contributing

Contributions are what make the open source community such an amazing place to learn, inspire, and create. Any contributions you make are **greatly appreciated**.

If you have a suggestion that would make this better, please fork the repo and create a pull request. You can also simply open an issue with the tag "enhancement".
Don't forget to give the project a star! Thanks again!

1. Fork the Project
2. Create your Feature Branch (`git checkout -b feature/AmazingFeature`)
3. Commit your Changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the Branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

<p align="right">(<a href="#top">back to top</a>)</p>



<!-- LICENSE -->
## License

Distributed under the GPL-3.0 license. See [LICENSE](https://github.com/OpenScan-org/OpenScan3/blob/main/LICENSE) for more information.

<p align="right">(<a href="#top">back to top</a>)</p>



<!-- CONTACT -->
## Contact

Join the OpenScan [Discord Server](https://discord.com/invite/gpaKWPpWtG) to get in touch with the OpenScan community!

Or write an email to <a href="mailto:info@openscan.eu">info@openscan.eu</a>

<p align="right">(<a href="#top">back to top</a>)</p>




