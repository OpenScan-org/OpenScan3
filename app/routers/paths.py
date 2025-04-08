import io

from fastapi import APIRouter
from fastapi.responses import Response
from fastapi_versionizer import api_version

from app.services.paths import paths

router = APIRouter(
    prefix="/paths",
    tags=["paths"],
    responses={404: {"description": "Not found"}},
)


@api_version(0,1)
@router.get("/{method}", response_model=list[paths.CartesianPoint3D])
async def get_path(method: paths.PathMethod, points: int):
    """Get a list of coordinates by path method and number of points"""
    return paths.get_path(method, points)


@api_version(0,1)
@router.get("/{method}/preview")
def get_path(method: paths.PathMethod, points: int, highlight_point: int = None):
    """Visualize path and optionally highlight specified point"""
    image = paths.plot_points(paths.get_path(method, points), highlight_point)
    return Response(image, media_type="image/png")
