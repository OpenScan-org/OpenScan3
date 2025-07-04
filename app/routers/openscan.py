import asyncio
from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import StreamingResponse
from fastapi_versionizer import api_version

from typing import Tuple

from app.models.paths import PathMethod, PolarPoint3D
from app.controllers.hardware.motors import move_to_point
from app.controllers.device import get_scanner_model

router = APIRouter(
    prefix="",
    tags=["openscan"],
    responses={404: {"description": "Not found"}},
)


@api_version(0,1)
@router.get("/")
async def get_software_info():
    """Get information about the scanner software"""
    return {"model": get_scanner_model(),
            "firmware": "-"}


@api_version(0,1)
@router.put("/scanner-position")
async def move_to_position(point: PolarPoint3D):
    """Move Rotor and Turntable to a polar point"""
    await move_to_point(point)

