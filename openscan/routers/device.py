from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel
import os
import json
import tempfile
import shutil

from openscan.models.scanner import ScannerDevice
from openscan.controllers import device

from openscan.utils.settings import resolve_settings_dir
from openscan.routers.cameras import CameraStatusResponse
from openscan.routers.motors import MotorStatusResponse
from openscan.routers.lights import LightStatusResponse

router = APIRouter(
    prefix="/device",
    tags=["device"],
    responses={404: {"description": "Not found"}},
)


class DeviceConfigRequest(BaseModel):
    config_file: str

class DeviceStatusResponse(BaseModel):
    name: str
    model: str
    shield: str
    cameras: dict[str, CameraStatusResponse]
    motors: dict[str, MotorStatusResponse]
    lights: dict[str, LightStatusResponse]
    initialized: bool

class DeviceControlResponse(BaseModel):
    success: bool
    message: str
    status: DeviceStatusResponse


@router.get("/info", response_model=DeviceStatusResponse)
async def get_device_info():
    """Get information about the device

    Returns:
        dict: A dictionary containing information about the device
    """
    info = device.get_device_info()
    return DeviceStatusResponse.model_validate(info)


@router.get("/configurations", response_model=dict[str, list[str]])
async def list_config_files():
    """List all available device configuration files"""
    try:
        configs = device.get_available_configs()
        return {"status": "success", "configs": configs}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error listing configuration files: {str(e)}")


@router.post("/configurations/", response_model=DeviceControlResponse)
async def add_config_json(config_data: ScannerDevice, filename: DeviceConfigRequest):
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
        # Create a temporary file to save the configuration
        with tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w") as temp_file:
            # Convert the model to a dictionary and save it as JSON
            config_dict = config_data.dict()
            json.dump(config_dict, temp_file, indent=4)
            temp_path = temp_file.name

        # Save to settings directory with a meaningful name
        settings_dir = resolve_settings_dir("device")
        os.makedirs(settings_dir, exist_ok=True)

        filename = f"{filename.config_file}.json"
        target_path = os.path.join(settings_dir, filename)

        # Move the temporary file to the target path
        shutil.move(temp_path, target_path)

        return DeviceControlResponse(
            success=True,
            message="Configuration saved successfully",
            status=DeviceStatusResponse.model_validate(device.get_device_info())
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error setting device configuration: {str(e)}")


@router.patch("/configurations/current", response_model=DeviceControlResponse)
async def save_device_config():
    """Save the current device configuration to a file

    This endpoint saves the current device configuration to device_config.json.

    Returns:
        dict: A dictionary containing the status of the operation
    """
    if device.save_device_config():
        return DeviceControlResponse(
            success=True,
            message="Configuration saved successfully",
            status=DeviceStatusResponse.model_validate(device.get_device_info())
        )
    else:
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
        if device.set_device_config(config_file):
            return DeviceControlResponse(
                success=True,
                message="Configuration loaded successfully",
                status=DeviceStatusResponse.model_validate(device.get_device_info())
            )
        else:
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
    try:
        device.initialize(detect_cameras=detect_cameras)
        return DeviceControlResponse(
            success=True,
            message="Hardware reinitialized successfully",
            status=DeviceStatusResponse.model_validate(device.get_device_info())
        )
    except Exception as e:
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