from typing import Optional
from fastapi import APIRouter, HTTPException
from fastapi.encoders import jsonable_encoder

from app.controllers import projects
from app.controllers.cameras import cameras
from app.models.project import Project

router = APIRouter(
    prefix="/projects",
    tags=["projects"],
    responses={404: {"description": "Not found"}},
)


@router.get("/", response_model=list[Project])
async def get_projects():
    return projects.get_projects()


@router.get("/{project_name}", response_model=Project)
async def get_project(project_name: str):
    try:
        return projects.get_project(project_name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Project {project_name} not found")

@router.delete("/{project_name}", response_model=bool)
async def delete_project(project_name: str):
    try:
        return projects.delete_project(projects.get_project(project_name))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Project {project_name} not found")


@router.post("/{project_name}", response_model=Project)
async def new_project(project_name: str):
    try:
        return projects.new_project(project_name)
    except ValueError:
        raise HTTPException(status_code=400, detail="Project {project_name} already exists")


@router.put("/{project_name}/photo", response_model=bool)
async def add_photo(project_name: str, camera_id: int):
    camera = cameras.get_camera(camera_id)
    camera_controller = cameras.get_camera_controller(camera)
    photo = camera_controller.photo(camera)
    return projects.add_photo(projects.get_project(project_name), photo)
