"""
Tests for the projects API endpoints.
"""
import shutil
import tempfile
from typing import Generator

import pytest
from fastapi.testclient import TestClient

from app.controllers.services.projects import ProjectManager, get_project_manager
from app.main import app
from app.models.project import Project


@pytest.fixture(scope="module")
def project_manager() -> Generator[ProjectManager, None, None]:
    """
    Create a ProjectManager instance with a temporary directory for projects.
    """
    temp_dir = tempfile.mkdtemp()
    pm = ProjectManager(path=temp_dir)
    yield pm
    shutil.rmtree(temp_dir)


@pytest.fixture(scope="module")
def client(project_manager: ProjectManager) -> Generator[TestClient, None, None]:
    """
    Create a test client for the API, overriding the project_manager dependency.
    """
    app.dependency_overrides[get_project_manager] = lambda: project_manager
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture(scope="module")
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