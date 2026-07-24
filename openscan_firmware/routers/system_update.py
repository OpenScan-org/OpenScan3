"""System update API endpoints."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel

from openscan_firmware.system_update import (
    UpdateConflictError,
    read_user_update_status,
    refresh_user_update_status,
    run_user_update_apply,
)

router = APIRouter(
    prefix="/system/update",
    tags=["system update"],
    responses={404: {"description": "Not found"}},
)


class OpenScanUpdatePackage(BaseModel):
    """One optional OpenScan component update for the details view."""

    id: Literal["firmware", "client", "updater", "system_config", "camera_stack"]
    installed_version: str | None
    available_version: str | None
    update_available: bool


class OpenScanUpdateSummary(BaseModel):
    updates_available: bool
    packages: list[OpenScanUpdatePackage]


class SystemUpdateSummary(BaseModel):
    updates_available: bool
    count: int
    reboot_required_after_install: bool


class UpdateStatusResponse(BaseModel):
    """Cached, user-facing software update status."""

    status: Literal[
        "unknown",
        "up_to_date",
        "updates_available",
        "status_unavailable",
        "check_failed",
    ]
    checked_at: str | None
    stale: bool
    release_channel: Literal["stable", "nightly", "unknown"]
    openscan: OpenScanUpdateSummary
    system: SystemUpdateSummary
    reboot_required: bool


class UpdateInstallResponse(BaseModel):
    """Result of a synchronous user-requested update installation."""

    status: Literal["completed", "install_failed", "install_blocked"]
    reboot_required: bool

@router.get("/status", response_model=UpdateStatusResponse)
async def get_update_status(response: Response) -> dict:
    status_code, payload = await read_user_update_status()
    response.status_code = status_code
    return payload

@router.post("/check", response_model=UpdateStatusResponse)
async def check_for_updates(response: Response) -> dict:
    status_code, payload = await refresh_user_update_status()
    response.status_code = status_code
    return payload


@router.post("/apply", response_model=UpdateInstallResponse)
async def apply_updates(response: Response) -> dict:
    try:
        status_code, payload = await run_user_update_apply()
    except UpdateConflictError as exc:
        payload = {
            "status": "install_blocked",
            "reboot_required": False,
        }
        status_code = 409
    response.status_code = status_code
    return payload
