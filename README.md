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

## Install OpenScan Image (Recommended)

1. Download the latest image from [openscan.eu](https://openscan.eu/pages/resources-downloads)
2. Write the image to an SD card using dd (unzip first) or Raspi Imager (without custom settings!)

or patch your own image: https://github.com/esto-openscan/OpenScan3-image-patcher

#### Accessing OpenScan3

*   **Hostname:** `openscan3-alpha`
*   **Web Interface:** Currently there is only API documentation available. Open a web browser on a device on the same network and navigate to `http://openscan3-alpha:8000/docs` or to `http://openscan3-alpha:8000/latest/docs` for a cleaner overview of API-Endpoints
*   **SSH (if enabled):** `ssh pi@openscan3-alpha` (Password: `raspberry`)

#### First Steps After Boot

Once the Raspberry Pi has completed its final reboot and OpenScan3 is running, you need to load a device configuration specific to your hardware setup. By default, no specific configuration is loaded.

There are two ways to load a configuration:

**Method 1: Using the API (Recommended)**

1.  Navigate to the API documentation (usually accessible via a link like `/docs` or `/redoc` on the web interface, so `http://openscan3-alpha:8000/latest/docs`).
3.  Find the **Device** Section and the **PUT** endpoint `/latest/device/configurations/current`.
4.  Use the "Try it out" feature.
5.  In the **Request body**, enter the name of the configuration file you want to load. For example, for an OpenScan Mini with a Greenshield, use:
    ```json
    {
      "config_file": "default_mini_greenshield.json"
    }
    ```
    *(You can find available default configuration files in the `/home/pi/OpenScan3/app/config/hardware_configurations` directory on the Raspberry Pi)*
6.  Execute the request. If successful, you should receive a `200 OK` response, and the hardware corresponding to the configuration should initialize.

**Method 2: Manual File Copy**

1.  Connect to your Raspberry Pi via SSH: `ssh pi@openscan3-alpha` (Password: `raspberry`).
2.  Navigate to the OpenScan3 directory: `cd /home/pi/OpenScan3/`
3.  List the available default configurations: `ls settings/`
4.  Choose the configuration file that matches your hardware (e.g., `default_mini_greenshield.json`).
5.  Copy the chosen configuration file to `device_config.json` in the main OpenScan3 directory:
    ```bash
    cp settings/default_mini_greenshield.json device_config.json
    ```
6.  Restart the OpenScan3 service for the changes to take effect:
    ```bash
    sudo systemctl restart openscan3.service
    ```
    Alternatively, you can simply reboot the Raspberry Pi: `sudo reboot`

After loading the correct configuration, your OpenScan hardware should be ready to use via the web interface.

## Install manually

#### Prerequisites

 - Raspberry Pi 4 running the latest image of Raspberry OS Lite
 - OpenScan Mini

#### Installation

1. Install dependencies

```sh
sudo apt-get update && sudo apt-get install git libgphoto2-dev libcap-dev python3-dev python3-libcamera python3-kms++ python3-opencv -y

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
#### Start OpenScan3

Activate your virtual environment and run the main script.

To start the api backend run:
```sh
export PYTHONPATH=$(pwd)
python app/main.py
```

Now the api should be accessible from `http://local_ip:8000`

To access an api documentation go to `http://local_ip:8000/docs`

Follow the first steps after boot in the previous section.

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




