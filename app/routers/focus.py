from fastapi import APIRouter, Body

from app.controllers.focus import Focuser
from app.controllers import projects
from app.controllers import cloud

router = APIRouter(
    prefix="/focus",
    tags=["focus"],
    responses={404: {"description": "Not found"}},
)

@router.get("/read_Focus")
async def read_focus():
    return Focuser('/dev/v4l-subdev1').read()

@router.post("/write_Focus")
async def write_focus(focus_value: int):
    return Focuser('/dev/v4l-subdev1').write(value=focus_value)