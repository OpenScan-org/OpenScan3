from fastapi import APIRouter
from fastapi_versionizer import api_version

from openscan.controllers.services import projects, cloud

router = APIRouter(
    prefix="/cloud",
    tags=["cloud"],
    responses={404: {"description": "Not found"}},
)


@api_version(0,1)
@router.get("/")
async def get_cloud():
    return cloud.get_token_info()


@api_version(0,1)
@router.get("/{project_name}")
async def get_project(project_name: str):
    return cloud.get_project_info(project_name)


@api_version(0,1)
@router.post("/{project_name}")
async def upload_project(project_name: str):
    project = projects.get_project(project_name)
    cloud.upload_project(project.name)
