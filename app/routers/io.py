from fastapi import APIRouter, Response
from fastapi.encoders import jsonable_encoder

from app.controllers import gpio

router = APIRouter(
    prefix="/io",
    tags=["io"],
    responses={404: {"description": "Not found"}},
)


@router.get("/")
async def get_pins():
    return gpio.get_pins()


@router.get("/{pin_id}")
async def get_pin(pin_id: int):
    return gpio.get_pin(pin_id)


@router.put("/{pin_id}")
async def set_pin(pin_id: int, status: bool):
    return gpio.set_pin(pin_id, status)


@router.put("/{pin_id}/toggle")
async def toggle_pin(pin_id: int):
    return gpio.toggle_pin(pin_id)
