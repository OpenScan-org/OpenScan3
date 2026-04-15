"""OpenScan Hardware Manager Module

This module is responsible for initializing and managing hardware components
like cameras, motors, and lights. It also handles different scanner models
and their specific configurations.
"""

import json
import logging
import os
import pathlib
import asyncio
from copy import deepcopy
from pathlib import Path
from typing import Dict, List, Optional
from importlib import resources
from dotenv import load_dotenv

from linuxpy.video.device import iter_video_capture_devices
import gphoto2 as gp

from openscan_firmware.controllers.hardware.interfaces import HardwareEvent

from openscan_firmware.models.camera import Camera, CameraType
from openscan_firmware.models.motor import Motor, Endstop
from openscan_firmware.models.light import Light
from openscan_firmware.models.trigger import Trigger
from openscan_firmware.models.scanner import (
    ScannerDevice,
    ScannerDeviceConfig,
    PersistedCameraConfig,
    PersistedEndstopConfig,
    ScannerModel,
    ScannerShield,
    ScannerStartupMode,
    ScannerCalibrateMode,
)

from openscan_firmware.config.camera import CameraSettings
from openscan_firmware.config.motor import MotorConfig
from openscan_firmware.config.light import LightConfig
from openscan_firmware.config.endstop import EndstopConfig
from openscan_firmware.config.trigger import TriggerConfig
from openscan_firmware.config.cloud import (
    load_cloud_settings_from_env,
    set_cloud_settings,
    mask_secret,
)
from openscan_firmware.controllers.services.cloud_settings import (
    load_persistent_cloud_settings,
    set_active_source,
)

from openscan_firmware.controllers.hardware.cameras.camera import (
    create_camera_controller,
    get_all_camera_controllers,
    get_available_camera_types,
    is_camera_type_available,
    remove_camera_controller,
)
from openscan_firmware.controllers.hardware.motors import create_motor_controller, get_all_motor_controllers, get_motor_controller, \
    remove_motor_controller
from openscan_firmware.controllers.hardware.lights import create_light_controller, get_all_light_controllers, remove_light_controller, \
    get_light_controller
from openscan_firmware.controllers.hardware.triggers import (
    create_trigger_controller,
    get_all_trigger_controllers,
    remove_trigger_controller,
)
from openscan_firmware.controllers.hardware.endstops import EndstopController
from openscan_firmware.controllers.hardware.gpio import cleanup_all_pins

from openscan_firmware.controllers.services.projects import get_project_manager
from openscan_firmware.controllers.services.device_events import schedule_device_status_broadcast
from openscan_firmware.utils.dir_paths import (
    resolve_settings_dir,
    resolve_settings_file,
    resolve_settings_path,
)
from openscan_firmware.utils.firmware_state import mark_clean_shutdown

from openscan_firmware.utils.inactivity_timer import inactivity_timer, inactivity_timer_paused

import time

logger = logging.getLogger(__name__)

# Current scanner model

def _create_default_scanner_device() -> ScannerDevice:
    device = ScannerDevice(
        name="Unknown device",
        model=None,
        shield=None,
        cameras={},
        motors={},
        lights={},
        triggers={},
        endstops={},
    )
    # beware, PrivateAttr are NOT initialized in constructor
    # nor an error message is shown...
    device._idle = False
    device._initialized = False
    return device


_scanner_device = _create_default_scanner_device()
_FACTORY_DEFAULT_CONFIG = ScannerDeviceConfig(
    name="Unknown device",
    model=None,
    shield=None,
    cameras={},
    motors={},
    lights={},
    triggers={},
    endstops={},
    scan_radius_mm=1.0,
).model_dump(mode="json")

# Path to device configuration file (persisted)
BASE_DIR = pathlib.Path(__file__).parent.parent.parent
SETTINGS_DIR = resolve_settings_dir("device")
DEVICE_CONFIG_FILE = resolve_settings_file("device", "device_config.json")


def _runtime_to_persisted_config() -> ScannerDeviceConfig:
    return ScannerDeviceConfig(
        name=_scanner_device.name,
        model=_scanner_device.model.value if _scanner_device.model else None,
        shield=_scanner_device.shield.value if _scanner_device.shield else None,
        cameras={
            name: PersistedCameraConfig(
                type=cam.type,
                path=cam.path,
                settings=cam.settings,
            )
            for name, cam in _scanner_device.cameras.items()
        },
        motors={name: motor.settings for name, motor in _scanner_device.motors.items()},
        lights={name: light.settings for name, light in _scanner_device.lights.items()},
        triggers={name: trigger.settings for name, trigger in _scanner_device.triggers.items()},
        endstops={
            name: PersistedEndstopConfig(settings=endstop.settings)
            for name, endstop in _scanner_device.endstops.items()
        },
        motors_timeout=_scanner_device.motors_timeout,
        scan_radius_mm=_scanner_device.scan_radius_mm,
        startup_mode=_scanner_device.startup_mode.value if _scanner_device.startup_mode else None,
        calibrate_mode=_scanner_device.calibrate_mode.value if _scanner_device.calibrate_mode else None,
    )


def load_device_config(config_file=None) -> dict:
    """Load device configuration from a file

    Args:
        config_file: Path to configuration file to load as preset.
                     If None, loads from device_config.json or default_minimal_config.json

    Returns:
        bool: True if configuration was loaded successfully
    """
    # populate default config dictionary from factory defaults
    config_dict = deepcopy(_FACTORY_DEFAULT_CONFIG)

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

    persisted_config = ScannerDeviceConfig.model_validate(config_dict)
    return persisted_config.model_dump(mode="json")


def save_device_config() -> bool:
    """Save the current device configuration to device_config.json"""
    #global _scanner_device

    try:
        os.makedirs(os.path.dirname(DEVICE_CONFIG_FILE), exist_ok=True)

        config_to_save = _runtime_to_persisted_config().model_dump(mode="json")

        with open(DEVICE_CONFIG_FILE, "w") as f:
            json.dump(config_to_save, f, indent=4)

        logger.info(f"Device configuration saved successfully to {DEVICE_CONFIG_FILE}")
        return True
    except Exception as e:
        logger.error(f"Error saving device configuration: {e}", exc_info=True)
        return False


async def set_device_config(config_file) -> bool:
    """Set the device configuration from a file and initialize hardware.

    Args:
        config_file: Path or filename to the configuration file

    Returns:
        bool: True if successful, False otherwise
    """

    resolved_path = resolve_settings_path("device", config_file)
    if not resolved_path.exists():
        logger.error(
            "Requested device configuration file does not exist",
            extra={"requested": str(config_file), "resolved_path": str(resolved_path)},
        )
        return False

    logger.info(
        "Loading device configuration from file",
        extra={"requested": str(config_file), "resolved_path": str(resolved_path)},
    )

    config = load_device_config(str(resolved_path))
    await initialize(config)

    if not save_device_config():
        logger.error(
            "Failed to persist device configuration after loading %s", resolved_path
        )
        return False

    logger.info(
        "Device configuration applied",
        extra={"requested": str(config_file), "resolved_path": str(resolved_path)},
    )
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
        "lights": {name: controller.get_status() for name, controller in get_all_light_controllers().items()},
        "triggers": {name: controller.get_status() for name, controller in get_all_trigger_controllers().items()},

        "motors_timeout": _scanner_device.motors_timeout,
        "scan_radius_mm": _scanner_device.scan_radius_mm,
        "startup_mode": _scanner_device.startup_mode,
        "calibrate_mode": _scanner_device.calibrate_mode,

        "idle": _scanner_device._idle,
        "initialized": _scanner_device._initialized
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


def _load_trigger_config(settings: dict) -> TriggerConfig:
    """Load trigger configuration for the current model."""
    try:
        return TriggerConfig(**settings)
    except Exception as e:
        logger.error("Error loading trigger settings: ", e)
        raise


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
                    path=str(cam.filename),
                    settings=CameraSettings()
                )
            cam.close()
    except Exception as e:
        logger.error(f"Error loading Linux cameras: {e}")

    # Get GPhoto2 cameras (avoid deprecated CameraList iteration)
    try:
        gphoto2_cameras = gp.Camera.autodetect()
        for idx in range(gphoto2_cameras.count()):
            camera_name = gphoto2_cameras.get_name(idx)
            camera_path = gphoto2_cameras.get_value(idx)
            cameras[camera_name] = Camera(
                type=CameraType.GPHOTO2,
                name=camera_name,
                path=camera_path,
                settings=CameraSettings(),
            )
    except Exception as e:
        logger.error(f"Error loading GPhoto2 cameras: {e}")

    # Get Picamera2
    if is_camera_type_available(CameraType.PICAMERA2):
        try:
            from picamera2 import Picamera2

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
            logger.critical(
                "Error loading Picamera2, most likely because of incorrect dtoverlay in /boot/firmware/config.txt.",
                exc_info=True,
            )
        except Exception as e:
            logger.error(f"Error loading Picamera2: {e}", exc_info=True)
    else:
        logger.info("Skipping Picamera2 detection: module not available on this system.")
    return cameras


def _build_configured_camera_objects(config_cameras: dict) -> Dict[str, Camera]:
    camera_objects: Dict[str, Camera] = {}
    for cam_name, cam_conf in config_cameras.items():
        camera = Camera(
            name=cam_name,
            type=CameraType(cam_conf["type"]),
            path=cam_conf["path"],
            settings=_load_camera_config(cam_conf["settings"]),
        )
        camera_objects[cam_name] = camera
    return camera_objects


def _merge_detected_with_configured(
    configured: Dict[str, Camera],
    detected: Dict[str, Camera],
) -> Dict[str, Camera]:
    """Merge freshly detected cameras into configured cameras.

    - Keep configured cameras and settings as baseline.
    - Add newly detected cameras that are not configured yet.
    - For existing names, keep configured settings but refresh type/path from detection.
    """
    merged = dict(configured)

    for name, detected_camera in detected.items():
        if name in merged:
            configured_camera = merged[name]
            merged[name] = Camera(
                name=name,
                type=detected_camera.type,
                path=detected_camera.path,
                settings=configured_camera.settings,
            )
            continue
        merged[name] = detected_camera

    return merged

""" Inactivity code -- allow to send device (parts) to sleep when idle for some time
"""
# check if device is idle
def is_idle() -> bool:
    global _scanner_device
    return _scanner_device._idle
    
# send device to idle mode
def go_to_idle() -> None:

    global _scanner_device
    _scanner_device._idle = True

    for _, controller in get_all_motor_controllers().items():
        controller.refresh()

    for _, controller in get_all_light_controllers().items():
        controller.refresh()

    inactivity_timer.stop()
    logger.info("Device gone to sleep")
    
# resume device normal operation
async def resume_from_idle() -> None:

    global _scanner_device

    logger.info("Resuming from IDLE")

    _scanner_device._idle = False
    
    # refresh idle state in controllers
    for _, controller in get_all_motor_controllers().items():
        controller.refresh()
        
    for _, controller in get_all_light_controllers().items():
        controller.refresh()

    await asyncio.sleep(0.1)
    inactivity_timer.start()
    
    logger.info("Device awakened from sleep")

# recalibrate all motors
async def recalibrate_motors():
    logger.info("Calibratong motors")
    for _, controller in get_all_motor_controllers().items():
        if not controller._calibrated:
            await controller.calibrate()

# handle hardware event from controllers
async def handle_idle_event(event: HardwareEvent):

    # don't if device is not fully initialized
    # to avoid exiting from idle mode when setting up initial state
    if not _scanner_device._initialized:
        logger.info("Device not fully initialized - can't resume from idle")
        return

    # an event shall exit from idle mode
    if is_idle():
        await resume_from_idle()
        if _scanner_device.calibrate_mode == ScannerCalibrateMode.CALIBRATE_ON_WAKE:
            await recalibrate_motors()

    match event:
        case HardwareEvent.MOVE_EVENT:
            logger.info("MOVE EVENT")
            return

        case HardwareEvent.HOME_EVENT:
            logger.info("HOME EVENT")
            if _scanner_device.calibrate_mode == ScannerCalibrateMode.CALIBRATE_ON_HOME:
                await recalibrate_motors()

        case HardwareEvent.LIGHT_EVENT:
            logger.info("LIGHT EVENT")
   
        case _:
            logger.info("UNKNOWN EVENT")
 
async def initialize(config: dict | ScannerDeviceConfig | None = None, detect_cameras: bool = False):
    """Detect and load hardware components.

    Args:
        config: Optional configuration dictionary. When not provided, loads the
            currently active device configuration from disk.
        detect_cameras: Whether to force camera auto-detection.
    """

    if config is None:
        config = load_device_config()

    await _initialize_with_config(config, detect_cameras)


async def _initialize_with_config(config: dict | ScannerDeviceConfig, detect_cameras: bool = False):
    """Internal helper that assumes the configuration dict is already resolved."""
    global _scanner_device
    config_dict = ScannerDeviceConfig.model_validate(config).model_dump(mode="json")
    # Load environment variables
    load_dotenv()

    # if already initialized, remove all controllers for reinitializing
    if _scanner_device._initialized:
        logger.debug("Hardware already initialized. Cleaning up old controllers.")
        for controller in get_all_motor_controllers():
            remove_motor_controller(controller)
        for controller in get_all_light_controllers():
            remove_light_controller(controller)
        for controller in get_all_trigger_controllers():
            remove_trigger_controller(controller)
        for controller in get_all_camera_controllers():
            remove_camera_controller(controller)
        cleanup_all_pins()
        logger.debug("Cleaned up old controllers.")

    # Detect hardware
    configured_cameras = _build_configured_camera_objects(config_dict["cameras"])

    if detect_cameras or not configured_cameras:
        camera_objects = _detect_cameras()
    else:
        camera_objects = configured_cameras
        # Always attempt best-effort augmentation so newly attached USB cameras
        # appear without requiring a full config reset.
        detected_cameras = _detect_cameras()
        camera_objects = _merge_detected_with_configured(camera_objects, detected_cameras)
        newly_added = [name for name in camera_objects.keys() if name not in configured_cameras]
        if newly_added:
            logger.info("Detected additional cameras not in config: %s", ", ".join(newly_added))

    # Create motor objects
    motor_objects = {}
    for motor_name in config_dict["motors"]:
        motor = Motor(name=motor_name,
        settings=_load_motor_config(config_dict["motors"][motor_name]))
        motor_objects[motor_name] = motor
        logger.debug(f"Loaded motor {motor_name} with settings: {motor.settings}")

    # Create light objects
    light_objects = {}
    for light_name in config_dict["lights"]:
        light = Light(
            name=light_name,
            settings=_load_light_config(config_dict["lights"][light_name])
        )
        light_objects[light_name] = light
        logger.debug(f"Loaded light {light_name} with settings: {light.settings}")

    # Create trigger objects
    trigger_objects = {}
    for trigger_name in config_dict["triggers"]:
        trigger = Trigger(
            name=trigger_name,
            settings=_load_trigger_config(config_dict["triggers"][trigger_name])
        )
        trigger_objects[trigger_name] = trigger
        logger.debug(f"Loaded trigger {trigger_name} with settings: {trigger.settings}")

    # Cloud settings
    persistent_settings = load_persistent_cloud_settings()
    if persistent_settings:
        set_cloud_settings(persistent_settings)
        set_active_source("persistent")
        logger.info(
            "Cloud service configured from persisted settings for host %s (user %s).",
            persistent_settings.host,
            mask_secret(persistent_settings.user),
        )
    else:
        cloud_settings = load_cloud_settings_from_env()
        if cloud_settings:
            set_cloud_settings(cloud_settings)
            set_active_source("environment")
            logger.info(
                "Cloud service configured from environment for host %s (user %s).",
                cloud_settings.host,
                mask_secret(cloud_settings.user),
            )
        else:
            set_cloud_settings(None)
            set_active_source(None)
            logger.warning(
                "Cloud service not configured. Set OPENSCANCLOUD_USER, OPENSCANCLOUD_PASSWORD and OPENSCANCLOUD_TOKEN to enable uploads."
            )

    # Initialize controllers
    availability = get_available_camera_types()
    for name, camera in camera_objects.items():
        try:
            if not availability.get(camera.type, False):
                logger.warning(
                    "Skipping controller init for %s (%s): dependency not available.",
                    name,
                    camera.type,
                )
                continue
            create_camera_controller(camera)
        except Exception as e:
            logger.error(f"Error initializing camera controller for {name}: {e}")

    for name, motor in motor_objects.items():
        try:
            controller = create_motor_controller(motor)
            controller.set_idle_callbacks(is_idle, handle_idle_event)
        except Exception as e:
            logger.error(f"Error initializing motor controller for {name}: {e}")

    # Create endstop objects
    endstop_objects = {}
    if "endstops" in config_dict:
        for endstop_name in config_dict["endstops"]:
            try:
                settings = _load_endstop_config(config_dict["endstops"][endstop_name]["settings"])
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
            controller = create_light_controller(light)
            controller.set_idle_callbacks(is_idle, handle_idle_event)
        except Exception as e:
            logger.error(f"Error initializing light controller for {name}: {e}")

    for name, trigger in trigger_objects.items():
        try:
            create_trigger_controller(trigger)
        except Exception as e:
            logger.error(f"Error initializing trigger controller for {name}: {e}")

    # initialize project manager
    try:
        project_manager = get_project_manager()
    except Exception as e:
        logger.error(f"Error initializing project manager: {e}", exc_info=True)

    # turn on lights
    for _, controller in get_all_light_controllers().items():
        await controller.turn_on()

    _scanner_device = ScannerDevice(
        name=config_dict["name"],
        model=ScannerModel(config_dict.get("model")) if config_dict.get("model") else None,
        shield=ScannerShield(config_dict.get("shield")) if config_dict.get("shield") else None,
        cameras=camera_objects,
        motors=motor_objects,
        lights=light_objects,
        triggers=trigger_objects,
        endstops=endstop_objects,

        # motors timeout in seconds - 0 to disable
        motors_timeout=config_dict["motors_timeout"],
        scan_radius_mm=config_dict["scan_radius_mm"],
        
        startup_mode=config_dict["startup_mode"],
        calibrate_mode=config_dict["calibrate_mode"],
    )
    
    # beware, PrivateAttr are NOT initialized in constructor
    # nor an error message is shown...
    _scanner_device._initialized=True

    # initialize inactivity timer
    if _scanner_device.motors_timeout > 0:
        inactivity_timer.set_timeout(_scanner_device.motors_timeout)
        inactivity_timer.on_timeout = go_to_idle
        inactivity_timer.enable()
        logger.info(f"Inactivity timer set to {_scanner_device.motors_timeout} seconds.")
    else:
        inactivity_timer.disable()
        logger.info("Inactivity timer disabled.")
        
    # initialize sleep mode
    if _scanner_device.startup_mode == ScannerStartupMode.STARTUP_ENABLED:
        _scanner_device._idle = True
        logger.info("Starting in active mode.")
        await resume_from_idle()
    else:
        _scanner_device._idle = False
        logger.info("Starting in idle mode.")
        go_to_idle()

    logger.info("Hardware initialized.")
    logger.debug(f"Initialized ScannerDevice: {_scanner_device.model_dump(mode='json')}.")
    schedule_device_status_broadcast()


def get_scan_radius_mm() -> float:
    """Return the configured scan radius in millimeters."""
    return float(_scanner_device.scan_radius_mm)


def get_available_configs():
    """Get a list of all available device configuration files

    Returns:
        list: List of dictionaries with information about each config file
    """
    configs: list[dict] = []

    settings_dir = resolve_settings_dir("device")
    if not settings_dir.exists():
        fallback_dir = resolve_settings_dir()
        if fallback_dir.exists():
            settings_dir = fallback_dir
        else:
            return configs

    for file in settings_dir.iterdir():
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

    return configs


def reboot(with_saving = False):
    if with_saving:
        save_device_config()
    cleanup_and_exit()
    os.system("systemctl reboot")


def shutdown(with_saving = False):
    if with_saving:
        save_device_config()
    cleanup_and_exit()
    os.system("systemctl poweroff")


def cleanup_and_exit():
    cam_controllers = get_all_camera_controllers()
    for name, controller in cam_controllers.items():
        try:
            controller.cleanup()
            logger.debug(f"Camera controller '{name}' closed successfully.")
        except Exception as e:
            logger.error(f"Error closing camera controller '{name}': {e}")

    cleanup_all_pins()
    mark_clean_shutdown()
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
