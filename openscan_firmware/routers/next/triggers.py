from datetime import datetime

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, Field

from openscan_firmware.config.trigger import TriggerConfig
from openscan_firmware.controllers.hardware.triggers import get_all_trigger_controllers, get_trigger_controller
from .settings_utils import create_settings_endpoints


router = APIRouter(
    prefix="/triggers",
    tags=["triggers"],
    responses={404: {"description": "Not found"}},
)


class TriggerStatusResponse(BaseModel):
    name: str
    busy: bool
    settings: TriggerConfig
    last_triggered_at: datetime | None = None
    last_completed_at: datetime | None = None
    last_duration_ms: int | None = None


class TriggerExecutionRequest(BaseModel):
    pre_trigger_delay_ms: int = Field(default=0, ge=0, le=30_000)
    post_trigger_delay_ms: int = Field(default=0, ge=0, le=30_000)


class TriggerExecutionResponse(BaseModel):
    name: str
    triggered_at: datetime
    completed_at: datetime
    duration_ms: int


@router.get("/", response_model=dict[str, TriggerStatusResponse])
async def get_triggers():
    return {
        name: controller.get_status()
        for name, controller in get_all_trigger_controllers().items()
    }


@router.get("/{trigger_name}", response_model=TriggerStatusResponse)
async def get_trigger(trigger_name: str):
    try:
        return get_trigger_controller(trigger_name).get_status()
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{trigger_name}/trigger", response_model=TriggerExecutionResponse)
async def trigger_once(
    trigger_name: str,
    request: TriggerExecutionRequest | None = Body(default=None),
):
    request = request or TriggerExecutionRequest()
    try:
        controller = get_trigger_controller(trigger_name)
        execution = await controller.trigger(
            pre_trigger_delay_ms=request.pre_trigger_delay_ms,
            post_trigger_delay_ms=request.post_trigger_delay_ms,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return TriggerExecutionResponse(
        name=trigger_name,
        triggered_at=execution.triggered_at,
        completed_at=execution.completed_at,
        duration_ms=execution.duration_ms,
    )


create_settings_endpoints(
    router=router,
    resource_name="trigger_name",
    get_controller=get_trigger_controller,
    settings_model=TriggerConfig,
)
