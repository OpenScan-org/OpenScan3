from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel, ValidationError, Field, ConfigDict
from pathlib import Path
import os
import json
import tempfile
import shutil
import logging
from typing import Any

from openscan_firmware.models.scanner import ScannerDevice, ScannerStartupMode, ScannerCalibrateMode
from openscan_firmware.controllers import device

from openscan_firmware.utils.dir_paths import resolve_settings_dir
from .cameras import CameraStatusResponse
from .motors import MotorStatusResponse
from .lights import LightStatusResponse

router = APIRouter(
    prefix="/device",
    tags=["device"],
    responses={404: {"description": "Not found"}},
)

logger = logging.getLogger(__name__)


class DeviceConfigRequest(BaseModel):
    config_file: str

class DeviceStatusResponse(BaseModel):
    name: str
    model: str
    shield: str
    cameras: dict[str, CameraStatusResponse]
    motors: dict[str, MotorStatusResponse]
    lights: dict[str, LightStatusResponse]
    motors_timeout: float
    startup_mode: ScannerStartupMode
    calibrate_mode: ScannerCalibrateMode
    initialized: bool

class DeviceControlResponse(BaseModel):
    success: bool
    message: str
    status: DeviceStatusResponse


class DeviceConfigPayload(BaseModel):
    """Schema reflecting the persisted device configuration format."""

    model_config = ConfigDict(extra="ignore")

    name: str
    model: str | None = None
    shield: str | None = None
    cameras: dict[str, dict[str, Any]] = Field(default_factory=dict)
    motors: dict[str, dict[str, Any]] = Field(default_factory=dict)
    lights: dict[str, dict[str, Any]] = Field(default_factory=dict)
    endstops: dict[str, dict[str, Any]] | None = None
    motors_timeout: float = 0.0
    startup_mode: ScannerStartupMode | str = ScannerStartupMode.STARTUP_ENABLED
    calibrate_mode: ScannerCalibrateMode | str = ScannerCalibrateMode.CALIBRATE_MANUAL


@router.get("/info", response_model=DeviceStatusResponse)
async def get_device_info():
    """Get information about the device

    Returns:
        dict: A dictionary containing information about the device
    """
    try:
        info = device.get_device_info()
        return DeviceStatusResponse.model_validate(info)
    except ValidationError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "message": "Device configuration is not loaded.",
                "errors": exc.errors(),
            },
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting device info: {str(e)}")


@router.get("/configurations")
async def list_config_files():
    """List all available device configuration files"""
    try:
        configs = device.get_available_configs()
        return {"status": "success", "configs": configs}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error listing configuration files: {str(e)}")


@router.get("/configurations/current")
async def get_current_config():
    """Return the currently active device configuration file."""
    try:
        logger.debug("Reading current device configuration from %s", device.DEVICE_CONFIG_FILE)
        config_path = Path(device.DEVICE_CONFIG_FILE)
        config_payload = device.load_device_config()
        return {
            "status": "success",
            "filename": config_path.name,
            "path": str(config_path),
            "config": config_payload,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error loading current configuration: {exc}")


@router.get("/configurations/{filename}")
async def get_config_file(filename: str):
    """Return a specific configuration JSON file by filename."""
    try:
        logger.debug("Reading configuration file request", extra={"filename": filename})
        normalized = filename if filename.endswith(".json") else f"{filename}.json"
        safe_name = Path(normalized).name
        config_path = resolve_settings_dir("device") / safe_name

        if not config_path.exists():
            raise HTTPException(
                status_code=404,
                detail={
                    "message": f"Config file not found: {safe_name}",
                    "available_configs": device.get_available_configs(),
                },
            )

        try:
            config_payload = json.loads(config_path.read_text())
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Failed to parse configuration file '{safe_name}': {exc.msg}",
            )

        return {
            "status": "success",
            "filename": config_path.name,
            "path": str(config_path),
            "config": config_payload,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error loading configuration file: {exc}")


@router.post("/configurations/", response_model=DeviceControlResponse)
async def add_config_json(config_data: DeviceConfigPayload, filename: DeviceConfigRequest):
    """Add a device configuration from a JSON object

    This endpoint accepts a JSON object with the device configuration,
    validates it and saves it to a file.

    Args:
        config_data: The device configuration to add
        filename: The filename to save the configuration as

    Returns:
        dict: A dictionary containing the status of the operation
    """
    try:
        logger.info("Persisting uploaded configuration", extra={"filename": filename.config_file})
        # Create a temporary file to save the configuration
        with tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w") as temp_file:
            # Convert the model to a dictionary and save it as JSON
            config_dict = config_data.dict()
            json.dump(config_dict, temp_file, indent=4)
            temp_path = temp_file.name

        # Save to settings directory with a meaningful name
        settings_dir = resolve_settings_dir("device")
        os.makedirs(settings_dir, exist_ok=True)

        target_filename = f"{filename.config_file}.json"
        target_path = os.path.join(settings_dir, target_filename)

        # Move the temporary file to the target path
        shutil.move(temp_path, target_path)

        status = device.get_device_info()
        logger.info(
            "Configuration saved",
            extra={
                "filename": target_filename,
                "motors": list(status.get("motors", {}).keys()),
            },
        )

        return DeviceControlResponse(
            success=True,
            message="Configuration saved successfully",
            status=DeviceStatusResponse.model_validate(status)
        )

    except Exception as e:
        logger.exception("Error while saving configuration", extra={"filename": filename.config_file})
        raise HTTPException(status_code=500, detail=f"Error setting device configuration: {str(e)}")


@router.patch("/configurations/current", response_model=DeviceControlResponse)
async def save_device_config():
    """Save the current device configuration to a file

    This endpoint saves the current device configuration to device_config.json.

    Returns:
        dict: A dictionary containing the status of the operation
    """
    logger.info("Saving current runtime configuration to disk")
    if device.save_device_config():
        status = device.get_device_info()
        return DeviceControlResponse(
            success=True,
            message="Configuration saved successfully",
            status=DeviceStatusResponse.model_validate(status)
        )
    else:
        logger.error("save_device_config returned False")
        raise HTTPException(status_code=500, detail="Failed to save device configuration")

@router.put("/configurations/current", response_model=DeviceControlResponse)
async def set_config_file(config_data: DeviceConfigRequest):
    """Set the device configuration from a file and initialize hardware

    Args:
        config_data: The device configuration to set

    Returns:
        dict: A dictionary containing the status of the operation
    """
    try:
        logger.info("Setting active configuration", extra={"requested": config_data.config_file})
        # Get available configs
        available_configs = device.get_available_configs()

        # Check if the config file exists in available configs
        config_file = config_data.config_file
        config_found = False

        # If it's just a filename (no path), try to find it in available configs
        if not os.path.dirname(config_file):
            for config in available_configs:
                if config["filename"] == config_file:
                    config_file = config["path"]
                    config_found = True
                    break
        else:
            # Check if the full path exists
            config_found = os.path.exists(config_file)

        if not config_found:
            raise HTTPException(
                status_code=404,
                detail={
                    "message": f"Config file not found: {config_data.config_file}",
                    "available_configs": available_configs
                }
            )

        # Set device config
        if await device.set_device_config(config_file):
            status = device.get_device_info()
            logger.info("Configuration loaded", extra={"active": config_file})
            return DeviceControlResponse(
                success=True,
                message="Configuration loaded successfully",
                status=DeviceStatusResponse.model_validate(status)
            )
        else:
            logger.error("set_device_config returned False", extra={"active": config_file})
            raise HTTPException(status_code=500, detail="Failed to load device configuration")

    except HTTPException:
        # Re-raise HTTP exceptions to preserve status code and detail
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error setting device configuration: {str(e)}")


@router.post("/configurations/current/initialize", response_model=DeviceControlResponse)
async def reinitialize_hardware(detect_cameras: bool = False):
    """Reinitialize hardware components

    This can be used in case of a hardware failure or to reload the hardware components.

    Args:
        detect_cameras: Whether to detect cameras

    Returns:
        dict: A dictionary containing the status of the operation
    """
    logger.info("Reinitializing hardware", extra={"detect_cameras": detect_cameras})
    try:
        await device.initialize(detect_cameras=detect_cameras)
        status = device.get_device_info()
        logger.info(
            "Hardware reinitialized",
            extra={
                "detect_cameras": detect_cameras,
                "motors": list(status.get("motors", {}).keys()),
                "lights": list(status.get("lights", {}).keys()),
            },
        )
        return DeviceControlResponse(
            success=True,
            message="Hardware reinitialized successfully",
            status=DeviceStatusResponse.model_validate(status)
        )
    except Exception as e:
        logger.exception("Error reloading hardware", extra={"detect_cameras": detect_cameras})
        raise HTTPException(status_code=500, detail=f"Error reloading hardware: {str(e)}")


@router.post("/reboot", response_model=bool)
def reboot(save_config: bool = False):
    """Reboot system and optionally save config.

    Args:
        save_config: Whether to save the current configuration before rebooting
    """
    device.reboot(save_config)
    return True


@router.post("/shutdown", response_model=bool)
def shutdown(save_config: bool = False) -> None:
    """Shutdown system and optionally save config.

    Args:
        save_config: Whether to save the current configuration before shutting down
    """
    device.shutdown(save_config)
    return True
