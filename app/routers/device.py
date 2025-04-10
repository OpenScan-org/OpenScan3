from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi_versionizer import api_version
from pydantic import BaseModel
import os
import json
import tempfile
import shutil

from app.models.scanner import ScannerDevice
from app.controllers import device

router = APIRouter(
    prefix="/device",
    tags=["device"],
    responses={404: {"description": "Not found"}},
)


class DeviceConfigRequest(BaseModel):
    config_file: str


@api_version(0,1)
@router.get("/info")
async def get_device_info():
    """Get information about the device"""
    return device.get_device_info()


@api_version(0,1)
@router.get("/configurations")
async def list_config_files():
    """List all available device configuration files"""
    try:
        configs = device.get_available_configs()
        return {"status": "success", "configs": configs}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error listing configuration files: {str(e)}")


@api_version(0,1)
@router.post("/configurations/")
async def add_config_json(config_data: ScannerDevice, filename: DeviceConfigRequest):
    """Add a device configuration from a JSON object

    This endpoint accepts a JSON object with the device configuration,
    validates it and saves it to a file.
    """
    try:
        # Create a temporary file to save the configuration
        with tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w") as temp_file:
            # Convert the model to a dictionary and save it as JSON
            config_dict = config_data.dict()
            json.dump(config_dict, temp_file, indent=4)
            temp_path = temp_file.name

        # Save to settings directory with a meaningful name
        settings_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "settings")
        os.makedirs(settings_dir, exist_ok=True)

        filename = f"{filename.config_file}.json"
        target_path = os.path.join(settings_dir, filename)

        # Move the temporary file to the target path
        shutil.move(temp_path, target_path)

        return {"success": True,
                "message": "Configuration saved successfully",
                }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error setting device configuration: {str(e)}")


@api_version(0,1)
@router.patch("/configurations/current")
async def save_device_config():
    if device.update_and_save_device_config():
        return {"status": "success",
                "message": "Configuration saved successfully",
                "info": device.get_device_info()}
    return device.save_device_config()


@api_version(0,1)
@router.put("/configurations/current")
async def set_config_file(config_data: DeviceConfigRequest):
    """Set the device configuration from a file"""
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
            return {"status": "success", "info": device.get_device_info()}
        else:
            raise HTTPException(status_code=500, detail="Failed to load device configuration")

    except HTTPException:
        # Re-raise HTTP exceptions to preserve status code and detail
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error setting device configuration: {str(e)}")


@api_version(0,1)
@router.post("/configurations/current/initialize")
async def reinitialize_hardware(detect_cameras: bool = False):
    """Reinitialize hardware components"""
    try:
        device.initialize(detect_cameras)
        return {"status": "success", "info": device.get_device_info()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reloading hardware: {str(e)}")


@api_version(0,1)
@router.post("/reboot")
def reboot(save_config: bool = False):
    """Reboot system"""
    device.reboot(save_config)


@api_version(0,1)
@router.post("/shutdown")
def shutdown(save_config: bool = False):
    """Shutdown system"""
    device.shutdown(save_config)