"""OpenScan Hardware Manager Module

This module is responsible for initializing and managing hardware components
like cameras, motors, and lights. It also handles different scanner models
and their specific configurations.
"""

import json
import os
import pathlib
from typing import Dict, List, Optional
from dotenv import load_dotenv

from linuxpy.video.device import iter_video_capture_devices
import gphoto2 as gp
from picamera2 import Picamera2

from app.models.camera import Camera, CameraType
from app.models.motor import Motor, Endstop
from app.models.light import Light
from app.models.scanner import ScannerDevice, ScannerModel, ScannerShield

from app.config.camera import CameraSettings
from app.config.motor import MotorConfig
from app.config.light import LightConfig
from app.config.endstop import EndstopConfig
from app.config.cloud import CloudSettings

from app.controllers.hardware.cameras.camera import create_camera_controller, get_all_camera_controllers, remove_camera_controller
from app.controllers.hardware.motors import create_motor_controller, get_all_motor_controllers, get_motor_controller, remove_motor_controller
from app.controllers.hardware.lights import create_light_controller, get_all_light_controllers, remove_light_controller
from app.controllers.hardware.endstops import EndstopController
from app.controllers.hardware.gpio import cleanup_all_pins


# Current scanner model
_scanner_device = ScannerDevice(
    name="Unknown device",
    model=None,
    shield=None,
    cameras={},
    motors={},
    lights={},
    endstops={},
    initialized=False
)

# Path to device configuration file
BASE_DIR = pathlib.Path(__file__).parent.parent.parent
DEVICE_CONFIG_FILE = BASE_DIR / "settings" / "device_config.json"
DEFAULT_CAMERA_SETTINGS_FILE = BASE_DIR / "settings" / "default_camera_settings.json"


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
        # No file specified, try to load device_config.json
        if not os.path.exists(DEVICE_CONFIG_FILE):
            # If device_config.json doesn't exist, safe default config as device_config.json
            with open(DEVICE_CONFIG_FILE, "w") as f:
                json.dump(config_dict, f, indent=4)
            print("No device configuration found. Loading default configuration.")
        config_file = DEVICE_CONFIG_FILE
    try:
        print(f"Loading device configuration from: {config_file}")
        if os.path.exists(config_file):
            with open(config_file, "r") as f:
                loaded_config_from_file = json.load(f)
                config_dict.update(loaded_config_from_file)

                # if a config is specified, save it as device_config.json
                if config_file != DEVICE_CONFIG_FILE:
                    with open(DEVICE_CONFIG_FILE, "w") as f:
                        json.dump(config_dict, f, indent=4)
            print(f"Loaded device configuration: {config_dict['name']}")
    except Exception as e:
        print(f"Error loading device configuration: {e}")

    return config_dict


def save_device_config() -> bool:
    """Save the current device configuration to device_config.json"""
    #global _scanner_device

    try:
        os.makedirs(os.path.dirname(DEVICE_CONFIG_FILE), exist_ok=True)

        serialized_device = _scanner_device.model_dump(mode='json')

        with open(DEVICE_CONFIG_FILE, "w") as f:
            json.dump(serialized_device, f, indent=4)

        print(f"Saved device configuration to: {DEVICE_CONFIG_FILE}")
        return True
    except Exception as e:
        print(f"Error saving device configuration: {e}")
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
        print("Error loading camera settings: ", e)
        return CameraSettings()

def _load_motor_config(settings: dict) -> MotorConfig:
    """Load motor configuration for the current model"""
    try:
        return MotorConfig(**settings)
    except Exception as e:
        # Return default settings if error occured
        print("Error loading motor settings: ", e)
        return MotorConfig()

def _load_light_config(settings: dict) -> LightConfig:
    """Load light configuration for the current model"""
    try:
        pins = settings.get("pins")
        pin = settings.get("pin")
        if pin is not None and pins is None:
            settings["pins"] = [pin]
        elif pins is None:
            settings["pins"] = []
        return LightConfig(**settings)
    except Exception as e:
        # Return default settings if error occured
        print("Error loading light settings: ", e)
        return LightConfig()

def _load_endstop_config(settings: dict) -> EndstopConfig:
    """Load endstop configuration"""
    try:
        return EndstopConfig(**settings)
    except Exception as e:
        # Return default settings if error occured
        print("Error loading endstop settings: ", e)
        return EndstopConfig()

def _detect_cameras() -> Dict[str, Camera]:
    """Get a list of available cameras"""
    print("Loading cameras...")

    global _scanner_device

    for camera_controller in get_all_camera_controllers():
        remove_camera_controller(camera_controller)

    cameras = {}

    # Load default camera settings
    with open(DEFAULT_CAMERA_SETTINGS_FILE, "r") as f:
        camera_default_settings = json.load(f)

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
        print(f"Error loading Linux cameras: {e}")

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
        print(f"Error loading GPhoto2 cameras: {e}")

    # Get Picamera2
    try:
        picam = Picamera2()
        picam_name = picam.camera_properties.get("Model")
        cameras[picam_name] = Camera(
            type=CameraType.PICAMERA2,
            name=picam_name,
            path="/dev/video" + str(picam.camera_properties.get("Location")),
            settings=_load_camera_config(camera_default_settings[picam_name])
        )
        picam.close()
        del picam

    except Exception as e:
        print(f"Error loading Picamera2: {e}")

    return cameras


def initialize(config: dict = _scanner_device.model_dump(mode='json'), detect_cameras = False):
    """Detect and load hardware components"""
    global _scanner_device
    # Load environment variables
    load_dotenv()

    # if already initialized, remove all controllers for reinitializing
    if _scanner_device.initialized:
        for controller in get_all_motor_controllers():
            remove_motor_controller(controller)
        for controller in get_all_light_controllers():
            remove_light_controller(controller)
        print("Cleaned up old controllers.")

    # Detect hardware
    if detect_cameras:
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

    # Create light objects
    light_objects = {}
    for light_name in config["lights"]:
        light = Light(
            name=config["lights"][light_name]["name"],
            settings=_load_light_config(config["lights"][light_name])
        )
        light_objects[light_name] = light

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
            print(f"Error initializing camera controller for {name}: {e}")

    for name, motor in motor_objects.items():
        try:
            create_motor_controller(motor)
        except Exception as e:
            print(f"Error initializing motor controller for {name}: {e}")

    # Create endstop objects
    endstop_objects = {}
    for endstop_name in config["endstops"]:
        settings = _load_endstop_config(config["endstops"][endstop_name])
        try:
            endstop = Endstop(name=config["endstops"][endstop_name],
                              settings=_load_endstop_config(config["endstops"][endstop_name]))
            endstop_controller = EndstopController(endstop, controller=get_motor_controller(settings.motor_name))
            endstop_objects[endstop_name] = endstop
            endstop_controller.start_listener()
        except Exception as e:
            print(f"Error initializing endstop '{endstop_name}': {e}")

    for name, light in light_objects.items():
        try:
            create_light_controller(light)
        except Exception as e:
            print(f"Error initializing light controller for {name}: {e}")

    _scanner_device = ScannerDevice(
        name=config["name"],
        model=config["model"],
        shield=config["shield"],
        cameras=camera_objects,
        motors=motor_objects,
        lights=light_objects,
        endstops=endstop_objects,
        initialized=True
    )

def get_available_configs():
    """Get a list of all available device configuration files

    Returns:
        list: List of dictionaries with information about each config file
    """
    configs = []
    settings_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "settings")

    if not os.path.exists(settings_dir):
        return configs

    for file in os.listdir(settings_dir):
        if file.endswith(".json"):
            file_path = os.path.join(settings_dir, file)
            try:
                with open(file_path, "r") as f:
                    config = json.load(f)
                    configs.append({
                        "filename": file,
                        "path": file_path,
                        "name": config.get("name", "Unknown"),
                        "model": config.get("model", "Unknown"),
                        "shield": config.get("shield", "Unknown")
                    })
            except:
                # If we can't read the file, just add the basic info
                configs.append({
                    "filename": file,
                    "path": file_path
                })

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
    cleanup_all_pins()
    print("Exiting now...")