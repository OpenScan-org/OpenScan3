<div id="top"></div>

# OpenScan3

<!-- ABOUT THE PROJECT -->
## About The Project

OpenScan3 is a firmware for controlling OpenScan devices, a family of open source and open hardware devices, designed to make 3d scanning with photogrammetry accessible to everyone.

The goal of OpenScan3 is providing a hackable and extensible firmware for common OpenScan devices and a starting point for custom photogrammetry rigs.

OpenScan3 is maintained by [OpenScan.eu](https://openscan.eu).

OpenScan3 is under development and is not ready for production!

<p align="right">(<a href="#top">back to top</a>)</p>

<!-- GETTING STARTED -->
## Getting Started

There are two ways to get started on a Raspberry Pi: flash a ready-made image or build a custom one.

## Install OpenScan Image (Recommended)

> **Note:** Advanced customization (hostname, user, Wi‑Fi, etc.) is confirmed to work with Raspberry Pi Imager > 2.0.
> Older versions may not apply the customizations properly.

1. Open Raspberry Pi Imager (>=2.0.6).
2. Click **ADD OPTIONS** -> Click **EDIT** Content Repository -> Use custom URL and paste `https://openscan.eu/rpi-repo.json` -> Click **Apply and restart**
3. Choose your Raspberry Pi device
4. Select the image according to your camera variant. **IMPORTANT**: Ensure the image matches your camera model. Choosing the wrong image may result in permanent hardware damage. 
5. Select the storage device to write the image to.
6. Modify configuration options if needed (hostname, user, Wi‑Fi, etc.) via the Raspberry Pi Imager interface.
7. Write the image. Eject the card and insert it into the Pi.

**Default Hostname:** `openscan` (or `openscan.local` if mDNS is enabled)

**UI (Webfrontend):** http://openscan/ or http://openscan.local/

**API documentation:** http://openscan/api/latest/docs.

## Build OpenScan Image from Source

You can also use [OpenScan3 Pi Image Builder](https://github.com/esto-openscan/OpenScan3-pi-gen) based on pi-gen to build the image from source or customize it.

<p align="right">(<a href="#top">back to top</a>)</p>

## Development

See [`docs/DEVELOP.md`](docs/DEVELOP.md) for development setup, first steps, and architectural overview.


For details and up-to-date status, see GitHub issues and check out the Discord channel.

<p align="right">(<a href="#top">back to top</a>)</p>



<!-- CONTRIBUTING -->
## Contributing

OpenScan thrives because of its community. Whether you report a bug, suggest a feature, or submit a pull request: every contribution matters! Check out the [Contributor Guide](docs/CONTRIBUTING.md) to get started and jump right in!

<p align="right">(<a href="#top">back to top</a>)</p>



<!-- LICENSE -->
## License

Distributed under the GPL-3.0 license. See [LICENSE](https://github.com/OpenScan-org/OpenScan3/blob/main/LICENSE) for more information.

<p align="right">(<a href="#top">back to top</a>)</p>



<!-- CONTACT -->
## Contact

Join the OpenScan [Discord Server](https://discord.gg/eBdqtdkXyF) to get in touch with the OpenScan community!

Or write an email to <a href="mailto:info@openscan.eu">info@openscan.eu</a>

<p align="right">(<a href="#top">back to top</a>)</p>




