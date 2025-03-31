import asyncio
from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import StreamingResponse

from typing import Tuple

from app.models.paths import PathMethod, PolarPoint3D
from controllers.services import projects, scans
from app.controllers.device import get_scanner_model

router = APIRouter(
    prefix="",
    tags=["openscan"],
    responses={404: {"description": "Not found"}},
)


@router.get("/")
async def get_software_info():
    """Get information about the scanner software"""
    return {"model": get_scanner_model(),
            "firmware": "-"}


@router.put("/scanner-position")
async def move_to_position(point: PolarPoint3D):
    """Move Rotor and Turntable to a polar point"""
    await scans.move_to_point(point)

