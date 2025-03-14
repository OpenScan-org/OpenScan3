import asyncio
from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import StreamingResponse

from typing import Tuple

from app.models.paths import PathMethod, PolarPoint3D
from controllers.services import projects, scans
from app.services.paths import paths
from app.config.scan import ScanSetting

from app.config import config
router = APIRouter(
    prefix="",
    tags=["scanner"],
    responses={404: {"description": "Not found"}},
)


@router.get("/")
async def get_scanner():
    return {"status": "ok"}


@router.post("/move_to")
async def move_to_point(point: PolarPoint3D):
    await scans.move_to_point(point)


@router.post("/reboot")
def reboot():
    scans.reboot()

@router.post("/shutdown")
def shutdown():
    scans.shutdown()