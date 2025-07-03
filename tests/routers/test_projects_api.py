"""
Tests for the projects API endpoints.
"""
import shutil
import os
import tempfile
import json
from typing import Generator
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.controllers.services.projects import ProjectManager, get_project_manager
from app.main import app
from app.models.project import Project
from app.models.task import Task
from app.config.scan import ScanSetting


@pytest.fixture(scope="function")
def project_manager() -> Generator[ProjectManager, None, None]:
    """
    Create a ProjectManager instance with a temporary directory for projects.
    """
    temp_dir = tempfile.mkdtemp()
    pm = ProjectManager(path=temp_dir)
    yield pm
    shutil.rmtree(temp_dir)


@pytest.fixture(scope="function")
def client(project_manager: ProjectManager) -> Generator[TestClient, None, None]:
    """
    Create a test client for the API, overriding the project_manager dependency.
    """
    app.dependency_overrides[get_project_manager] = lambda: project_manager
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture(scope="function")
def test_project(project_manager: ProjectManager) -> Project:
    """
    Create a dummy project for testing and return it.
    """
    project = project_manager.add_project("test-project-123")
    return project


def test_get_project(client: TestClient, test_project: Project):
    """
    Test retrieving an existing project.
    """
    client.post(
     f"/latest/projects/{test_project.name}"
    )
    response = client.get(f"/latest/projects/{test_project.name}")
    assert response.status_code == 200
    project_data = response.json()
    assert project_data["name"] == test_project.name


def test_get_project_not_found(client: TestClient):
    """
    Test retrieving a non-existent project.
    """
    response = client.get("/latest/projects/non-existent-project")
    assert response.status_code == 404


def test_new_project(client: TestClient, project_manager: ProjectManager):
    """
    Test creating a new project.
    """
    project_name = "my-new-test-project"

    if client.get(f"/latest/projects/{project_name}"):
        client.delete(f"/latest/projects/{project_name}")

    project_description = "A description for the new project."
    response = client.post(
        f"/latest/projects/{project_name}?project_description={project_description}",
    )
    assert response.status_code == 200
    project_data = response.json()
    assert project_data["name"] == project_name
    assert project_data["description"] == project_description

    # Verify that the project directory and the project file were created
    project_path = os.path.join("projects", project_name)
    assert os.path.isdir(project_path)
    assert os.path.isfile(os.path.join(project_path, "openscan_project.json"))


def test_new_project_conflict(client: TestClient, test_project: Project):
    """
    Test creating a project that already exists.
    """
    response = client.post(
        f"/latest/projects/{test_project.name}?project_description={test_project.description}",
    )
    assert response.status_code == 400



# def test_get_all_projects(client: TestClient, project_manager: ProjectManager):
#     """
#     Test retrieving all projects.
#     """
#     # Create a few projects
#     project_manager.add_project("proj-1")
#     project_manager.add_project("proj-2")

#     response = client.get("/latest/projects/")
#     assert response.status_code == 200
#     projects_data = response.json()
#     assert len(projects_data) == 2
#     assert "proj-1" in projects_data
#     assert "proj-2" in projects_data


def test_delete_project(client: TestClient, test_project: Project):
    """
    Test deleting an existing project.
    """
    project_path = test_project.path
    assert os.path.isdir(project_path)

    # Delete the project
    response = client.delete(f"/latest/projects/{test_project.name}")
    assert response.status_code == 200

    # Verify the project is gone from the API
    response = client.get(f"/latest/projects/{test_project.name}")
    assert response.status_code == 404

    # Verify the project directory is gone from the filesystem
    #assert not os.path.isdir(project_path)


def test_delete_project_not_found(client: TestClient):
    """
    Test deleting a non-existent project.
    """
    response = client.delete("/latest/projects/non-existent-project-to-delete")
    assert response.status_code == 404