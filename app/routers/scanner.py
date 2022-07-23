from fastapi import APIRouter

router = APIRouter(
    prefix="/scanner",
    tags=["scanner"],
    responses={404: {"description": "Not found"}},
)


@router.get("/")
async def get_scanner():
    return {}
