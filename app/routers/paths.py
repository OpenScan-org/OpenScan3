import io

from fastapi import APIRouter

from app.services.paths import paths

router = APIRouter(
    prefix="/paths",
    tags=["paths"],
    responses={404: {"description": "Not found"}},
)


@router.get("/{method}", response_model=list[paths.Point3D])
async def get_path(method: paths.PathMethod, points: int):
    return paths.get_path(method, points)


@router.get("/{method}/preview", response_model=bytes)
async def get_path(method: paths.PathMethod, points: int):
    return paths.plot_points(paths.get_path(method, points))
