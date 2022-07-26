<div id="top"></div>

# OpenScan3

<!-- ABOUT THE PROJECT -->
## About The Project

OpenScan3 is a firmware for controlling OpenScan devices, a family of OpenSource and OpenHardware devices designed to make photogametry accessible to everyone.

<p align="right">(<a href="#top">back to top</a>)</p>

<!-- GETTING STARTED -->
## Getting Started

To run a copy of the firmware in your RPi you can follow of two routes

### Install OpenScanPi image (recommended)

TO BE DONE

### Install manually

#### Prerequisites

 - RPi running the latest image of Raspberry OS

#### Installation

1. Clone the repo

```sh
git clone https://github.com/OpenScan-org/OpenScan3.git
```

2. cd into the repo

```sh
cd OpenScan3
```

3. Install the necessary dependencies

```sh
pip install -r requirements.txt
```

<p align="right">(<a href="#top">back to top</a>)</p>

<!-- USAGE EXAMPLES -->
## Usage

To run the api backend run:
```sh
uvicorn app.main:app --host 0.0.0.0
```

Now the api should be accessible from `http://local_ip:8000`

To access an api playground go to `http://local_ip:8000/docs`

_For more information, please refer to the [Documentation](https://example.com)_

<p align="right">(<a href="#top">back to top</a>)</p>

<!-- ROADMAP -->
## Roadmap

- [ ] Full camera control
- [ ] Full motor and other hardware control
- [ ] Extra features
    - [ ] Network drive
    - [ ] ...

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

Distributed under the GPL-3.0 license. See `LICENSE` for more information.

<p align="right">(<a href="#top">back to top</a>)</p>



<!-- CONTACT -->
## Contact

TO BE DONE

<p align="right">(<a href="#top">back to top</a>)</p>




