from fastapi import APIRouter, Body

from app.controllers import lights
from app.models.light import LightType

router = APIRouter(
    prefix="/lights",
    tags=["lights"],
    responses={404: {"description": "Not found"}},
)


@router.get("/")
async def get_lights():
    return lights.get_lights()

@router.get("/{light_type}")
async def get_light(light_type: LightType):
    return lights.get_light(light_type)

@router.post("/{light_type}/turn_on")
async def turn_on_light(light_type: LightType):
    light = lights.get_light(light_type)
    lights.turn_light_on(light)

@router.post("/{light_type}/turn_off")
async def turn_off_light(light_type: LightType):
    light = lights.get_light(light_type)
    lights.turn_light_off(light)

@router.post("/{light_type}/toggle")
async def toggle_light(light_type: LightType):
    light = lights.get_light(light_type)
    if light.turned_on:
        lights.turn_light_off(light)
    else:
        lights.turn_light_on(light)