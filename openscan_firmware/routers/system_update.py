"""System update API endpoints."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

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


def _json(status_code: int, payload: dict) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=payload)


@router.get("/status")
async def get_update_status() -> JSONResponse:
    status_code, payload = await read_user_update_status()
    return _json(status_code, payload)

@router.post("/check")
async def check_for_updates() -> JSONResponse:
    status_code, payload = await refresh_user_update_status()
    return _json(status_code, payload)


@router.post("/apply")
async def apply_updates() -> JSONResponse:
    try:
        status_code, payload = await run_user_update_apply()
    except UpdateConflictError as exc:
        payload = {
            "status": "install_blocked",
            "reboot_required": False,
        }
        status_code = 409
    return _json(status_code, payload)
