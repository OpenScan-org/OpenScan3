from typing import Optional
from fastapi import APIRouter

from app.controllers import projects
from app.controllers import cloud

router = APIRouter(
    prefix="/cloud",
    tags=["cloud"],
    responses={404: {"description": "Not found"}},
)


@router.get("/")
async def get_cloud():
    return cloud.get_token_info()


@router.get("/{project_name}")
async def get_project(project_name: str):
    return cloud.get_project_info(project_name)


@router.post("/{project_name}")
async def upload_project(project_name: str):
    project = projects.get_project(project_name)
    cloud.upload_project(project.name)
