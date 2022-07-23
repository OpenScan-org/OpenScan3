from fastapi import APIRouter

from app.controllers import motors

router = APIRouter(
    prefix="/motors",
    tags=["motors"],
    responses={404: {"description": "Not found"}},
)


@router.get("/")
async def get_motors():
    return motors.get_motors()


@router.get("/{motor_id}")
async def get_motor(motor_id: str):
    return motors.get_motor(motor_id)

@router.post("/{motor_id}/move_to")
async def move_motor(motor_id: str, degrees: float):
    motors.move_motor_to(motor_id, degrees)

@router.post("/{motor_id}/move")
async def move_motor(motor_id: str, degrees: float):
    motors.move_motor_degrees(motor_id, degrees)
