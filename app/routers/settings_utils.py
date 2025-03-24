from typing import Any, Callable, Dict, Generic, Type, TypeVar
from fastapi import APIRouter, Body, HTTPException, Depends
from pydantic import BaseModel

T = TypeVar('T', bound=BaseModel)


def create_settings_endpoints(
        router: APIRouter,
        resource_name: str,
        get_controller: Callable[[str], Any],
        settings_model: Type[BaseModel]
):
    """
    Create standardized settings endpoints for a resource.

    Args:
        router: The FastAPI router to add endpoints to
        resource_name: Name of the resource (e.g., 'camera', 'motor')
        get_controller: Function to get the controller by name
        settings_model: Pydantic model for the settings
    """

    @router.get(f"/{{{resource_name}}}/settings")
    async def get_settings(name: str):
        """Get settings for a specific resource"""
        controller = get_controller(name)
        return controller.settings.model

    @router.put(f"/{{{resource_name}}}/settings")
    async def replace_settings(name: str, settings: settings_model):
        """Replace all settings for a specific resource"""
        controller = get_controller(name)
        try:
            controller.settings.replace(settings)
            return controller.settings.model
        except Exception as e:
            raise HTTPException(status_code=422, detail=str(e))

    @router.patch(f"/{{{resource_name}}}/settings")
    async def update_settings(name: str, settings: Dict[str, Any] = Body(...)):
        """Update specific settings for a resource"""
        controller = get_controller(name)
        try:
            controller.settings.update(**settings)
            return controller.settings.model
        except Exception as e:
            raise HTTPException(status_code=422, detail=str(e))

    return {
        "get_settings": get_settings,
        "replace_settings": replace_settings,
        "update_settings": update_settings
    }