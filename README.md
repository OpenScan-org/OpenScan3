<div id="top"></div>

# OpenScan3

<!-- ABOUT THE PROJECT -->
## About The Project

**OpenScan3 is under development and is not yet ready for use!**

For working firmware versions take a look at [OpenScan2](https://github.com/OpenScan-org/OpenScan2/) and [OpenScan-Meanwhile](https://github.com/stealthizer/OpenScan2).

OpenScan3 is a firmware for controlling OpenScan devices, a family of OpenSource and OpenHardware devices designed to make photogrammetry accessible to everyone.

<p align="right">(<a href="#top">back to top</a>)</p>

<!-- GETTING STARTED -->
## Getting Started

To run a copy of the firmware in your Raspberry Pi you can follow of two routes

### Install OpenScanPi image (recommended)

TO BE DONE

### Install manually

#### Prerequisites

 - Raspberry Pi 4 running the latest image of Raspberry OS Lite
 - OpenScan Mini (with "GreenShield")

#### Installation

1. Install dependencies

```sh
sudo apt-get update && sudo apt-get install git libgphoto2-dev libcav-dev python3-dev python3-libcamera python3-kms++ -y

```
Install libcamera-drivers according to the [Arducam Docs](https://docs.arducam.com/Raspberry-Pi-Camera/Native-camera/16MP-IMX519/#raspberry-pi-bullseye-os-6121-and-laterbookworm-os)

```sh
wget -O install_pivariety_pkgs.sh https://github.com/ArduCAM/Arducam-Pivariety-V4L2-Driver/releases/download/install_script/install_pivariety_pkgs.sh
chmod +x install_pivariety_pkgs.sh
./install_pivariety_pkgs.sh -p libcamera_dev
./install_pivariety_pkgs.sh -p libcamera_apps

```

2. Clone the repo

```sh
git clone https://github.com/OpenScan-org/OpenScan3.git
```

3. cd into the cloned repo

```sh
cd OpenScan3
```

4. Create a virtual environment (using system packages for picamera drivers) and activate it

```sh
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
```


5. Install the necessary pip packages

```sh
pip install -r requirements.txt
```

<p align="right">(<a href="#top">back to top</a>)</p>

<!-- USAGE EXAMPLES -->
## Usage

Activate your virtual environment and run the main script.

To run the api backend run:
```sh
export PYTHONPATH=$(pwd)
python app/main.py
```

Now the api should be accessible from `http://local_ip:8000`

To access an api playground go to `http://local_ip:8000/docs`

After reboot or logout be sure to activate your virtual environment again and set the correct paths:
```sh
cd OpenScan3/ && source .venv/bin/activate && export PYTHONPATH=$(pwd)
```

<p align="right">(<a href="#top">back to top</a>)</p>

<!-- ROADMAP -->
## Roadmap

There is no roadmap yet. Take a look at the [sketch of the software outline](https://github.com/OpenScan-org/OpenScan3/blob/main/software_dev_outline.md) for an overview instead.

See the [open issues](https://github.com/OpenScan-org/OpenScan3/issues) for a full list of proposed features (and known issues).

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




