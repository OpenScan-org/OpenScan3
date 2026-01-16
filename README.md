<div id="top"></div>

# OpenScan3

<!-- ABOUT THE PROJECT -->
## About The Project

OpenScan3 is a firmware for controlling OpenScan devices, a family of open source and open hardware devices, designed to make 3d scanning with photogrammetry accessible to everyone.

The goal of OpenScan3 is providing a hackable and extensible firmware for common OpenScan devices and a starting point for custom photogrammetry rigs.

OpenScan3 is under development and is not ready for production!

<p align="right">(<a href="#top">back to top</a>)</p>

<!-- GETTING STARTED -->
## Getting Started

There are two ways to get started on a Raspberry Pi: flash a ready-made image or build a custom one.

## Install OpenScan Image (Recommended)

Download the image from here: https://openscan.eu/pages/resources-downloads

Choose the image according to your camera variant:

- Arducam IMX519
- Arducam Hawkeye

Warning: Choosing the wrong image may result in permanent damage to your camera!

Flash the image with Raspberry Pi Imager or a similar tool.

**Default Hostname:** `openscan3-alpha` (or `openscan3-alpha.local` if mDNS is enabled)

**UI (Webfrontend):** http://openscan3-alpha/ or http://openscan3-alpha.local/

**API documentation:** http://openscan3-alpha/api/latest/docs.

## Build OpenScan Image from Source

You can also use [OpenScan3 Pi Image Builder](https://github.com/esto-openscan/OpenScan3-pi-gen) to build a customized image:

- Clone the repository with submodules: `git clone --recurse-submodules https://github.com/esto-openscan/OpenScan3-pi-gen.git`
- Pick a camera configuration (for example `./build-all.sh generic` for the generic variant)
- Flash the generated image from `pi-gen/deploy/` with Raspberry Pi Imager or a similar tool

Refer to the [user guide](https://github.com/esto-openscan/OpenScan3-pi-gen/blob/main/DOCUMENTATION.md) in that repository for detailed build and usage instructions, including networking, services, and troubleshooting.

<p align="right">(<a href="#top">back to top</a>)</p>

## Development

See [`docs/DEVELOP.md`](docs/DEVELOP.md) for development setup, first steps, and architectural overview.

<p align="right">(<a href="#top">back to top</a>)</p>

<!-- ROADMAP -->
## Roadmap

### Beta (February 2026)
- [x] WebSockets for tasks, device state, and scan progress
- [ ] OS/device services: Samba, USB, disk monitoring; camera-assisted Wi‑Fi/setup
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

Join the OpenScan [Discord Server](https://discord.gg/eBdqtdkXyF) to get in touch with the OpenScan community!

Or write an email to <a href="mailto:info@openscan_firmware.eu">info@openscan_firmware.eu</a>

<p align="right">(<a href="#top">back to top</a>)</p>




