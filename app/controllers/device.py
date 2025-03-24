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
from app.models.motor import Motor
from app.models.light import Light
from app.models.scanner import ScannerModel, ScannerShield

from app.config.camera import CameraSettings
from app.config.motor import MotorConfig
from app.config.light import LightConfig
from app.config.cloud import CloudSettings

from app.controllers.hardware.cameras.camera import create_camera_controller, get_all_camera_controllers
from app.controllers.hardware.motors import create_motor_controller, get_all_motor_controllers
from app.controllers.hardware.lights import create_light_controller, get_all_light_controllers


_initialized = False

# Hardware components
_cameras = {}
_motors = {}
_lights = {}

# Cloud settings
cloud = None
projects_path = pathlib.PurePath("projects")

# Current scanner model
_device_config = {}
current_model = None
current_shield = None

# Path to device configuration file
BASE_DIR = pathlib.Path(__file__).parent.parent.parent
DEFAULT_CONFIG_FILE = BASE_DIR / "settings" / "default_minimal_config.json"
DEVICE_CONFIG_FILE = BASE_DIR / "settings" / "device_config.json"


def load_device_config(config_file=None) -> bool:
    """Load device configuration from a file

    Args:
        config_file: Path to configuration file to load as preset.
                     If None, loads from device_config.json or default_minimal_config.json

    Returns:
        bool: True if configuration was loaded successfully
    """
    global current_model, current_shield, _device_config, _cameras, _motors, _lights
    # Determine which configuration file to load
    if config_file is None:
        # No file specified, try to load device_config.json
        if os.path.exists(DEVICE_CONFIG_FILE):
            config_file = DEVICE_CONFIG_FILE
        else:
            # Fall back to default.json if no device_config.json exists
            config_file = DEFAULT_CONFIG_FILE
    try:
        print(f"Loading device configuration from: {config_file}")
        if os.path.exists(config_file):
            with open(config_file, "r") as f:
                _device_config = json.load(f)

                # if a config is specified, save it as device_config.json
                if config_file != DEVICE_CONFIG_FILE:
                    with open(DEVICE_CONFIG_FILE, "w") as f:
                        json.dump(_device_config, f, indent=4)

                # Get device info
                current_model = ScannerModel(_device_config.get("model", "unknown"))
                current_shield = ScannerShield(_device_config.get("shield", "unknown"))

                # Get hardware devices
                _cameras = {}
                for cam_name, cam_settings in _device_config.get("cameras", {}).items():
                    _cameras[cam_name] = cam_settings

                _motors = {}
                for motor_name, motor_settings in _device_config.get("motors", {}).items():
                    _motors[motor_name] = motor_settings

                _lights = {}
                for light_name, light_settings in _device_config.get("lights", {}).items():
                    _lights[light_name] = light_settings

                print(f"Loaded device configuration: {_device_config.get('name', 'Unknown device')}")
                return True
    except Exception as e:
        print(f"Error loading device configuration: {e}")
        return False


def save_device_config() -> bool:
    """Save the current device configuration to device_config.json"""
    try:
        os.makedirs(os.path.dirname(DEVICE_CONFIG_FILE), exist_ok=True)

        with open(DEVICE_CONFIG_FILE, "w") as f:
            json.dump(_device_config, f, indent=4)

        print(f"Saved device configuration to: {DEVICE_CONFIG_FILE}")
        return True
    except Exception as e:
        print(f"Error saving device configuration: {e}")
        return False


def update_device_config() -> bool:
    """Update the device configuration with current hardware state

    This function updates the global _device_config object with the current
    state of all hardware components (cameras, motors, lights). Call this
    function after making changes to hardware settings before saving the config.

    Returns:
        bool: True if the update was successful, False otherwise
    """
    global _device_config

    try:
        # Update cameras configuration
        camera_controllers = get_all_camera_controllers()
        for cam_name, camera_data in _cameras.items():
            if cam_name in camera_controllers:
                _device_config["cameras"][cam_name]["settings"] = camera_controllers[cam_name].settings.dict()


        # Update motors configuration
        motor_controllers = get_all_motor_controllers()
        for motor_name, motor_data in _motors.items():
            if motor_name in motor_controllers:
                _device_config["motors"][motor_name]["settings"] = motor_controllers[motor_name].settings.dict()

        # Update lights configuration
        light_controllers = get_all_light_controllers()
        for light_name, light_data in _lights.items():
            if light_name in light_controllers:
                _device_config["lights"][light_name]["settings"] = light_controllers[light_name].settings.dict()

        return True
    except Exception as e:
        print(f"Error updating device configuration: {e}")
        return False


def update_and_save_device_config() -> bool:
    """Update the device configuration with current hardware state and save it

    This is a convenience function that calls update_device_config() and then
    save_device_config() to update and save the configuration in one step.

    Args:
        config_file: Path to save the configuration. If None, uses the current config file.

    Returns:
        bool: True if both update and save were successful, False otherwise
    """
    if update_device_config():
        return save_device_config()
    return False

def set_device_config(config_file) -> bool:
    """Set the device configuration from a file and initialize hardware

    Args:
        config_file: Path to the configuration file

    Returns:
        bool: True if successful, False otherwise
    """
    if load_device_config(config_file):
        initialize()
        return True
    return False

def get_scanner_model():
    """Get the current scanner model"""
    return current_model

def get_device_info():
    """Get information about the device"""
    return {
        "name": _device_config.get("name", "Unknown device"),
        "model": current_model.value if current_model else "unknown",
        "shield": current_shield or "unknown",
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
    # Get absolute path to settings
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

def _detect_cameras() -> List[Camera]:
    """Get a list of available cameras"""
    print("Loading cameras...")
    cameras = []

    # Get Linux cameras
    try:
        linuxpycameras = iter_video_capture_devices()
        for cam in linuxpycameras:
            cam.open()
            if cam.info.card not in ("unicam", "bcm2835-isp"):
                cameras.append(Camera(
                    type=CameraType.LINUXPY,
                    name=cam.info.card,
                    path=cam.filename,
                    settings=None
                ))
            cam.close()
    except Exception as e:
        print(f"Error loading Linux cameras: {e}")

    # Get GPhoto2 cameras
    try:
        gphoto2_cameras = gp.Camera.autodetect()
        for c in gphoto2_cameras:
            cameras.append(Camera(
                type=CameraType.GPHOTO2,
                name=c[0],
                path=c[1],
                settings=None
            ))
    except Exception as e:
        print(f"Error loading GPhoto2 cameras: {e}")

    # Get Picamera2
    try:
        picam = Picamera2()
        picam_name = picam.camera_properties.get("Model")
        cameras.append(Camera(
            type=CameraType.PICAMERA2,
            name=picam_name,
            path="/dev/video" + str(picam.camera_properties.get("Location")),
            settings=_load_camera_config(picam_name)
        ))
        picam.close()
        del picam
    except Exception as e:
        print(f"Error loading Picamera2: {e}")

    return cameras

def initialize(detect_cameras = False):
    """Detect and load hardware components"""
    global _cameras, _motors, _lights, _initialized, cloud
    # Load environment variables
    load_dotenv()

    # Detect hardware
    if detect_cameras:
        camera_objects = _detect_cameras()
    else:
        camera_objects = {}
        for cam_name in _cameras:
            camera = Camera(
                name=cam_name,
                type=CameraType(_cameras[cam_name]["type"]),
                path=_cameras[cam_name]["path"],
                settings=_load_camera_config(_cameras[cam_name]["settings"])
            )
            camera_objects[cam_name] = camera
        _cameras = camera_objects

    # Create motor objects
    motor_objects = {}
    for motor_name in _motors:
        motor = Motor(name=motor_name,
        settings=_load_motor_config(_motors[motor_name]))
        motor_objects[motor_name] = motor

    # Create light objects
    light_objects = {}
    for light_name in _lights:
        light = Light(
            name=_lights[light_name]["name"],
            settings=_load_light_config(_lights[light_name])
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

    for name, light in light_objects.items():
        try:
            create_light_controller(light)
        except Exception as e:
            print(f"Error initializing light controller for {name}: {e}")

    _initialized = True

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

# Load device config and initialize hardware
load_device_config()


def reboot(with_saving = False):
    if with_saving:
        save_device_config()
    os.system("sudo reboot")


def shutdown(with_saving = False):
    if with_saving:
        save_device_config()
    os.system("sudo shutdown now")