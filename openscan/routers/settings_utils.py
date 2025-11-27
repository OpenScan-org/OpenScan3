from typing import Any, Callable, Dict, Type, TypeVar
from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel

T = TypeVar('T', bound=BaseModel)


def create_settings_endpoints(
        router: APIRouter,
        resource_name: str,
        get_controller: Callable[[str], Any],
        settings_model: Type[T]
) -> Dict[str, Callable[..., Any]]:
    """
    Create standardized settings endpoints for a resource.

    Args:
        router: The FastAPI router to add endpoints to
        resource_name: Name of the resource (e.g., 'camera', 'motor')
        get_controller: Function to get the controller by name
        settings_model: Pydantic model for the settings
    """

    @router.get(
        f"/{{{resource_name}}}/settings",
        response_model=settings_model,
        name=f"get_{resource_name}_settings",
    )
    async def get_settings(name: str) -> T:
        """Get settings for a specific resource"""
        controller = get_controller(name)
        return controller.settings.model


    @router.put(
        f"/{{{resource_name}}}/settings",
        response_model=settings_model,
        name=f"replace_{resource_name}_settings",
    )
    async def replace_settings(name: str, settings: T) -> T:
        """Replace all settings for a specific resource

        Args:
            name: The name of the resource to replace settings for
            settings: The new settings for the resource

        Returns:
            The updated settings for the resource
        """
        controller = get_controller(name)
        try:
            controller.settings.replace(settings)
            return controller.settings.model
        except Exception as e:
            raise HTTPException(status_code=422, detail=str(e))


    @router.patch(
        f"/{{{resource_name}}}/settings",
        response_model=settings_model,
        name=f"update_{resource_name}_settings",
    )
    async def update_settings(
            name: str,
            settings: Dict[str, Any] = Body(..., examples=[{"some_setting": 123}])
    ) -> T:
        """Update one or more specific settings for a resource

        Args:
            name: The name of the resource to update settings for
            settings: A dictionary of settings to update

        Returns:
            The updated settings for the resource
        """
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