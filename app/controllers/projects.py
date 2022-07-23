from datetime import datetime
import io
import pathlib
from tempfile import TemporaryFile
from typing import IO
from zipfile import ZipFile
import orjson
import os
import shutil

from app.controllers.cameras import cameras
from app.models.camera import Camera

from app.models.project import Project, ProjectManifest
from app.config import config

ALLOWED_EXTENSIONS = (".jpg", ".jpeg", ".png")

def get_projects() -> list[Project]:
    return [
        get_project(folder)
        for folder in os.listdir(config.projects_path)
        if os.path.exists(
            config.projects_path.joinpath(folder, "openscan_project.json")
        )
    ]

def _get_project_path(project_name: str) -> pathlib.Path:
    return config.projects_path.joinpath(project_name)

def get_project(project_name: str) -> Project:
    project_path = _get_project_path(project_name)
    with open(project_path.joinpath("openscan_project.json")) as f:
        return Project(project_name, project_path, ProjectManifest(**orjson.loads(f.read())))


def delete_project(project: Project) -> bool:
    shutil.rmtree(project.path)

def save_project(project: Project):
    os.makedirs(project.path, exist_ok=True)
    with open(project.path.joinpath("openscan_project.json"), "wb") as f:
        f.write(orjson.dumps(project.manifest))

def new_project(project_name: str) -> Project:
    projects = get_projects()
    if project_name in [project.name for project in projects]:
        raise ValueError(f"Project {project_name} already exists")
    project_path = _get_project_path(project_name)
    project = Project(project_name, project_path, ProjectManifest(datetime.now()))
    save_project(project)
    return project


def get_project_photos(project: Project) -> list[str]:
    photos = [file for file in os.listdir(project.path) if file.lower().endswith(ALLOWED_EXTENSIONS)]
    return photos


def add_photo(project: Project, camera: Camera) -> bool:
    project_photos = get_project_photos(project)

    camera_controller = cameras.get_camera_controller(camera)
    photo = camera_controller.photo(camera)

    with open(
        project.path.joinpath(f"photo_{len(project_photos):>04}.jpg"), "wb"
    ) as f:
        f.write(photo.read())


def compress_project_photos(project: Project) -> IO[bytes]:
    photos = get_project_photos(project)
    file = TemporaryFile()
    with ZipFile(file, "w") as zipf:
        counter = 1
        for photo in photos:
            zipf.write(project.path.joinpath(photo), photo)
            print(f"{photo} - {counter}/{len(photos)}")
            counter += 1
    return file


def split_file(file: IO[bytes]) -> list[io.BytesIO]:
    file.seek(0, 2)
    size = file.tell()
    file.seek(0)

    chunk = file.read(config.cloud.split_size)
    while chunk:
        yield io.BytesIO(chunk)
        chunk = file.read(config.cloud.split_size)
