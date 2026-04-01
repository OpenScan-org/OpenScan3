"""Firmware settings API endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from openscan_firmware.config.firmware import (
    FirmwareSettings,
    get_firmware_settings,
    save_firmware_settings,
)

router = APIRouter(
    prefix="/firmware",
    tags=["firmware"],
    responses={404: {"description": "Not found"}},
)


class FirmwareSettingPatchRequest(BaseModel):
    value: Any


@router.get("/settings", response_model=FirmwareSettings)
async def get_settings() -> FirmwareSettings:
    """Return persisted firmware settings."""
    return get_firmware_settings()


@router.put("/settings", response_model=FirmwareSettings)
async def replace_settings(settings: FirmwareSettings) -> FirmwareSettings:
    """Replace the entire firmware settings payload."""
    save_firmware_settings(settings)
    return settings


@router.patch("/settings/{key}", response_model=FirmwareSettings)
async def update_setting(key: str, payload: FirmwareSettingPatchRequest) -> FirmwareSettings:
    """Update a single firmware settings key."""
    current_settings = get_firmware_settings()

    if key not in FirmwareSettings.model_fields:
        raise HTTPException(status_code=404, detail=f"Unknown firmware setting key: {key}")

    updated_payload = current_settings.model_dump()
    updated_payload[key] = payload.value
    updated_settings = FirmwareSettings.model_validate(updated_payload)

    save_firmware_settings(updated_settings)
    return updated_settings
