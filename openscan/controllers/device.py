"""OpenScan Hardware Manager Module

This module is responsible for initializing and managing hardware components
like cameras, motors, and lights. It also handles different scanner models
and their specific configurations.
"""

import json
import logging
import os
import pathlib
from pathlib import Path
from typing import Dict, List, Optional
from importlib import resources
from dotenv import load_dotenv

from linuxpy.video.device import iter_video_capture_devices
import gphoto2 as gp
from picamera2 import Picamera2

from openscan.models.camera import Camera, CameraType
from openscan.models.motor import Motor, Endstop
from openscan.models.light import Light
from openscan.models.scanner import ScannerDevice, ScannerModel, ScannerShield

from openscan.config.camera import CameraSettings
from openscan.config.motor import MotorConfig
from openscan.config.light import LightConfig
from openscan.config.endstop import EndstopConfig
from openscan.config.cloud import CloudSettings

from openscan.controllers.hardware.cameras.camera import create_camera_controller, get_all_camera_controllers, \
    remove_camera_controller
from openscan.controllers.hardware.motors import create_motor_controller, get_all_motor_controllers, get_motor_controller, \
    remove_motor_controller
from openscan.controllers.hardware.lights import create_light_controller, get_all_light_controllers, remove_light_controller, \
    get_light_controller
from openscan.controllers.hardware.endstops import EndstopController
from openscan.controllers.hardware.gpio import cleanup_all_pins

from openscan.controllers.services.projects import get_project_manager
from openscan.config.logger import load_settings_json, find_settings_file

logger = logging.getLogger(__name__)

# Current scanner model
_scanner_device = ScannerDevice(
    name="Unknown device",
    model=None,
    shield=None,
    cameras={},
    motors={},
    lights={},
    endstops={},
    initialized=False,
)

def _select_settings_dir() -> Path:
    """Select a writable settings directory by precedence.

    Precedence:
    1) OPENSCAN_SETTINGS_DIR (env var)
    2) /etc/openscan3/
    3) ./settings/
    """
    env_dir = os.getenv("OPENSCAN_SETTINGS_DIR")
    if env_dir:
        return Path(env_dir)
    etc_dir = Path("/etc/openscan3")
    if etc_dir.exists():
        return etc_dir
    return Path("./settings")


# Path to device configuration file (persisted)
BASE_DIR = pathlib.Path(__file__).parent.parent.parent
SETTINGS_DIR = _select_settings_dir()
DEVICE_CONFIG_FILE = SETTINGS_DIR / "device_config.json"
DEFAULT_CAMERA_SETTINGS_FILE = SETTINGS_DIR / "default_camera_settings.json"


def load_device_config(config_file=None) -> dict:
    """Load device configuration from a file

    Args:
        config_file: Path to configuration file to load as preset.
                     If None, loads from device_config.json or default_minimal_config.json

    Returns:
        bool: True if configuration was loaded successfully
    """
    # populate default config dictionary
    config_dict = _scanner_device.model_dump(mode='json')

    # Determine which configuration file to load
    if config_file is None:
        # No file specified, try to load device_config.json in selected settings dir
        try:
            DEVICE_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            logger.warning("Could not ensure settings directory exists: %s", DEVICE_CONFIG_FILE.parent)
        if not os.path.exists(DEVICE_CONFIG_FILE):
            # If device_config.json doesn't exist, save minimal model as starting point
            with open(DEVICE_CONFIG_FILE, "w") as f:
                json.dump(config_dict, f, indent=4)
            logger.warning("No device configuration found. Created default at %s.", DEVICE_CONFIG_FILE)
        config_file = str(DEVICE_CONFIG_FILE)
    try:
        logger.debug(f"Loading device configuration from: {config_file}")
        if os.path.exists(config_file):
            with open(config_file, "r") as f:
                loaded_config_from_file = json.load(f)
                config_dict.update(loaded_config_from_file)

                # if a config is specified, save it as device_config.json
                if config_file != DEVICE_CONFIG_FILE:
                    with open(DEVICE_CONFIG_FILE, "w") as f:
                        json.dump(config_dict, f, indent=4)
            logger.info(f"Loaded device configuration for: {config_dict['name']} with {config_dict['shield']}")
    except Exception as e:
        logger.error(f"Error loading device configuration: {e}")

    return config_dict


def save_device_config() -> bool:
    """Save the current device configuration to device_config.json"""
    #global _scanner_device

    try:
        os.makedirs(os.path.dirname(DEVICE_CONFIG_FILE), exist_ok=True)

        config_to_save = {
            "name": _scanner_device.name,
            "model": _scanner_device.model.value if _scanner_device.model else None,
            "shield": _scanner_device.shield.value if _scanner_device.shield else None,
            "cameras": {name: cam.model_dump(mode='json') for name, cam in _scanner_device.cameras.items()},
            "motors": {name: motor.settings.model_dump(mode='json') for name, motor in _scanner_device.motors.items()},
            "lights": {name: light.settings.model_dump(mode='json') for name, light in _scanner_device.lights.items()},
            "endstops": {name: endstop.model_dump(mode='json') for name, endstop in _scanner_device.endstops.items()}
        }

        with open(DEVICE_CONFIG_FILE, "w") as f:
            json.dump(config_to_save, f, indent=4)

        logger.info(f"Device configuration saved successfully to {DEVICE_CONFIG_FILE}")
        return True
    except Exception as e:
        logger.error(f"Error saving device configuration: {e}", exc_info=True)
        return False


def set_device_config(config_file) -> bool:
    """Set the device configuration from a file and initialize hardware

    Args:
        config_file: Path to the configuration file

    Returns:
        bool: True if successful, False otherwise
    """

    initialize(load_device_config(config_file))
    return True


def get_scanner_model():
    """Get the current scanner model"""
    return _scanner_device.model


def get_device_info():
    """Get information about the device"""
    return {
        "name": _scanner_device.name,
        "model": _scanner_device.model,
        "shield": _scanner_device.shield,
        "cameras": {name: controller.get_status() for name, controller in get_all_camera_controllers().items()},
        "motors": {name: controller.get_status() for name, controller in get_all_motor_controllers().items()},
        "lights": {name: controller.get_status() for name, controller in get_all_light_controllers().items()}
    }


def _load_camera_config(settings: dict) -> CameraSettings:
    try:
        return CameraSettings(**settings)
    except Exception as e:
        # Return default settings if error occured
        logger.error("Error loading camera settings: ", e)
        return CameraSettings()


def _load_motor_config(settings: dict) -> MotorConfig:
    """Load motor configuration for the current model"""
    try:
        return MotorConfig(**settings)
    except Exception as e:
        # Return default settings if error occured
        logger.error("Error loading motor settings: ", e)
        return MotorConfig()


def _load_light_config(settings: dict) -> LightConfig:
    """Load light configuration for the current model"""
    try:
        return LightConfig(**settings)
    except Exception as e:
        # Return default settings if error occured
        logger.error("Error loading light settings: ", e)
        return LightConfig()


def _load_endstop_config(settings: dict) -> EndstopConfig:
    """Helper function to load and validate endstop settings from a dictionary."""
    try:
        return EndstopConfig(**settings)
    except Exception as e:
        # Return default settings if error occured
        logger.error("Error loading endstop settings: ", e)
        return EndstopConfig()


def _detect_cameras() -> Dict[str, Camera]:
    """Get a list of available cameras"""
    logger.debug("Loading cameras...")

    global _scanner_device

    for camera_controller in get_all_camera_controllers():
        remove_camera_controller(camera_controller)

    cameras = {}

    # Get Linux cameras
    try:
        linuxpycameras = iter_video_capture_devices()
        for cam in linuxpycameras:
            cam.open()
            if cam.info.card not in ("unicam", "bcm2835-isp"):
                cameras[cam.info.card] = Camera(
                    type=CameraType.LINUXPY,
                    name=cam.info.card,
                    path=cam.filename,
                    settings=None
                )
            cam.close()
    except Exception as e:
        logger.error(f"Error loading Linux cameras: {e}")

    # Get GPhoto2 cameras
    try:
        gphoto2_cameras = gp.Camera.autodetect()
        for c in gphoto2_cameras:
            cameras[c[0]] = Camera(
                type=CameraType.GPHOTO2,
                name=c[0],
                path=c[1],
                settings=None
            )
    except Exception as e:
        logger.error(f"Error loading GPhoto2 cameras: {e}")

    # Get Picamera2
    try:
        picam = Picamera2()
        picam_name = picam.camera_properties.get("Model")
        cameras[picam_name] = Camera(
            type=CameraType.PICAMERA2,
            name=picam_name,
            path="/dev/video0" + str(picam.camera_properties.get("Location")),
            settings=CameraSettings()
        )
        picam.close()
        del picam

    except IndexError as e:
        logger.critical(f"Error loading Picamera2, most likely because of incorrect dtoverlay in /boot/firmware/config.txt.",
                     exc_info=True)
    except Exception as e:
        logger.error(f"Error loading Picamera2: {e}", exc_info=True)
    return cameras


def initialize(config: dict = _scanner_device.model_dump(mode='json'), detect_cameras = False):
    """Detect and load hardware components"""
    global _scanner_device
    # Load environment variables
    load_dotenv()

    # if already initialized, remove all controllers for reinitializing
    if _scanner_device.initialized:
        logger.debug("Hardware already initialized. Cleaning up old controllers.")
        for controller in get_all_motor_controllers():
            remove_motor_controller(controller)
        for controller in get_all_light_controllers():
            remove_light_controller(controller)
        logger.debug("Cleaned up old controllers.")

    # Detect hardware
    if detect_cameras or config["cameras"] == {}:
        camera_objects = _detect_cameras()
    else:
        camera_objects = {}
        for cam_name in config["cameras"]:
            camera = Camera(
                name=cam_name,
                type=CameraType(config["cameras"][cam_name]["type"]),
                path=config["cameras"][cam_name]["path"],
                settings=_load_camera_config(config["cameras"][cam_name]["settings"])
            )
            camera_objects[cam_name] = camera

    # Create motor objects
    motor_objects = {}
    for motor_name in config["motors"]:
        motor = Motor(name=motor_name,
        settings=_load_motor_config(config["motors"][motor_name]))
        motor_objects[motor_name] = motor
        logger.debug(f"Loaded motor {motor_name} with settings: {motor.settings}")

    # Create light objects
    light_objects = {}
    for light_name in config["lights"]:
        light = Light(
            name=light_name,
            settings=_load_light_config(config["lights"][light_name])
        )
        light_objects[light_name] = light
        logger.debug(f"Loaded light {light_name} with settings: {light.settings}")

    # Cloud settings
    cloud = CloudSettings(
        "openscan",
        "free",
        os.getenv("OPENSCANCLOUD_KEY"),
        "http://openscanfeedback.dnsuser.de:1334",
    )

    # Initialize controllers
    for name, camera in camera_objects.items():
        try:
            create_camera_controller(camera)
        except Exception as e:
            logger.error(f"Error initializing camera controller for {name}: {e}")

    for name, motor in motor_objects.items():
        try:
            create_motor_controller(motor)
        except Exception as e:
            logger.error(f"Error initializing motor controller for {name}: {e}")

    # Create endstop objects
    endstop_objects = {}
    if "endstops" in config:
        for endstop_name in config["endstops"]:
            try:
                settings = _load_endstop_config(config["endstops"][endstop_name]["settings"])
                endstop = Endstop(name=endstop_name, settings=settings)
                controller = get_motor_controller(settings.motor_name)
                if not controller:
                    raise ValueError(f"Motor '{settings.motor_name}' not found for endstop '{endstop_name}'")
                endstop_controller = EndstopController(endstop, controller=controller)
                endstop_objects[endstop_name] = endstop
                logging.debug(f"Loaded endstop {endstop_name} with settings: {endstop.settings}")
                endstop_controller.start_listener()
            except Exception as e:
                logger.error(f"Error initializing endstop '{endstop_name}': {e}")


    for name, light in light_objects.items():
        try:
            create_light_controller(light)
        except Exception as e:
            logger.error(f"Error initializing light controller for {name}: {e}")

    # initialize project manager
    try:
        project_manager = get_project_manager(BASE_DIR / "projects")
    except Exception as e:
        logger.error(f"Error initializing project manager: {e}", exc_info=True)

    # turn on lights
    for _, controller in get_all_light_controllers().items():
        controller.turn_on()

    _scanner_device = ScannerDevice(
        name=config["name"],
        model=ScannerModel(config.get("model")) if config.get("model") else None,
        shield=ScannerShield(config.get("shield")) if config.get("shield") else None,
        cameras=camera_objects,
        motors=motor_objects,
        lights=light_objects,
        endstops=endstop_objects,
        initialized=True
    )
    logger.info("Hardware initialized.")
    logger.debug(f"Initialized ScannerDevice: {_scanner_device.model_dump(mode='json')}.")


def get_available_configs():
    """Get a list of all available device configuration files

    Returns:
        list: List of dictionaries with information about each config file
    """
    configs: list[dict] = []

    def _append_from_dir(dir_path: Path):
        if not dir_path.exists():
            return
        for file in dir_path.iterdir():
            if file.suffix == ".json":
                try:
                    data = json.loads(file.read_text())
                    configs.append({
                        "filename": file.name,
                        "path": str(file),
                        "name": data.get("name", "Unknown"),
                        "model": data.get("model", "Unknown"),
                        "shield": data.get("shield", "Unknown")
                    })
                except Exception:
                    configs.append({"filename": file.name, "path": str(file)})

    # Precedence directories: env -> /etc/openscan3 -> ./settings
    for d in [Path(p) for p in (os.getenv("OPENSCAN_SETTINGS_DIR") or "").split(":") if p] + [Path("/etc/openscan3"), Path("./settings")]:
        _append_from_dir(d)

    # Packaged defaults
    try:
        pkg_dir = resources.files("openscan.resources.settings")
        for file in pkg_dir.iterdir():
            if file.name.endswith(".json"):
                try:
                    with file.open("r", encoding="utf-8") as f:
                        data = json.load(f)
                    configs.append({
                        "filename": file.name,
                        "path": f"package://openscan.resources.settings/{file.name}",
                        "name": data.get("name", "Unknown"),
                        "model": data.get("model", "Unknown"),
                        "shield": data.get("shield", "Unknown")
                    })
                except Exception:
                    configs.append({"filename": file.name, "path": f"package://openscan.resources.settings/{file.name}"})
    except Exception:
        logger.debug("Packaged settings not available.")

    return configs


def reboot(with_saving = False):
    if with_saving:
        save_device_config()
    os.system("sudo reboot")


def shutdown(with_saving = False):
    if with_saving:
        save_device_config()
    os.system("sudo shutdown now")


def cleanup_and_exit():
    cam_controllers = get_all_camera_controllers()
    for name, controller in cam_controllers.items():
        try:
            controller.cleanup()
            logger.debug(f"Camera controller '{name}' closed successfully.")
        except Exception as e:
            logger.error(f"Error closing camera controller '{name}': {e}")

    cleanup_all_pins()
    logger.info("Exiting now...")


def check_arducam_overlay(camera_model: str) -> bool:
    """Check if the arducam overlay is set for the given camera model

    Args:
        camera_model (str): The camera model to check for

    Returns:
        bool: True if the correct overlay is set, False otherwise
    """
    config_path = "/boot/firmware/config.txt"
    arducam_overlays = {
        "arducam_64mp": "dtoverlay=arducam-64mp",
        "imx519": "dtoverlay=imx519"
    }

    overlay = arducam_overlays.get(camera_model)

    try:
        with open( config_path, "r") as f:
            config_lines = f.read().splitlines()

        if overlay in config_lines:
            logger.debug(f"Overlay for {camera_model} is set: {overlay}")
            return True
        else:
            logger.error(f"Overlay for {camera_model} missing or wrong, should be: {overlay}")
            return False
    except Exception as e:
        logger.error(f"Error checking for arducam overlay in {config_path}: {e}")
        return False