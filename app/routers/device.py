from fastapi import APIRouter, HTTPException, UploadFile, File
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


@router.get("/info")
async def get_device_info():
    """Get information about the device"""
    return device.get_device_info()


@router.get("/config-files")
async def list_config_files():
    """List all available device configuration files"""
    try:
        configs = device.get_available_configs()
        return {"status": "success", "configs": configs}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error listing configuration files: {str(e)}")


@router.post("/config")
async def set_config_file(config_data: DeviceConfigRequest):
    """Set the device configuration from a file"""
    try:
        # Validate file exists
        if not os.path.exists(config_data.config_file):
            # Get available configs
            available_configs = device.get_available_configs()

            raise HTTPException(
                status_code=404,
                detail={
                    "message": f"Config file not found: {config_data.config_file}",
                    "available_configs": available_configs
                }
            )

        # Set device config
        if device.set_device_config(config_data.config_file):
            return {"status": "success", "info": device.get_device_info()}
        else:
            raise HTTPException(status_code=500, detail="Failed to load device configuration")

    except HTTPException:
        # Re-raise HTTP exceptions to preserve status code and detail
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error setting device configuration: {str(e)}")



@router.put("/config")
async def set_config_json(config_data: ScannerDevice):
    """Set the device configuration from a JSON object

    This endpoint accepts a JSON object with the device configuration,
    validates it, saves it to a file, and applies it to the device.
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

        # Create filename based on model and shield
        filename = f"{config_data.model.value}_{config_data.shield.value}.json"
        target_path = os.path.join(settings_dir, filename)

        # Move the temporary file to the target path
        shutil.move(temp_path, target_path)

        # Set device config
        if device.set_config_from_dict(config_data.dict(), target_path):
            return {
                "status": "success",
                "path": target_path,
                "info": device.get_device_info()
            }
        else:
            raise HTTPException(status_code=500, detail="Failed to load device configuration")

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error setting device configuration: {str(e)}")



@router.post("/reload")
async def reload_hardware():
    """Reload hardware detection and initialization"""
    try:
        device.initialize(detect_cameras=True)
        return {"status": "success", "info": device.get_device_info()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reloading hardware: {str(e)}")