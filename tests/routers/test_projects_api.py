"""
Tests for the projects API endpoints.
"""
import os
import shutil
import sys
import tempfile
import types
import uuid
from datetime import datetime
from pathlib import Path
from typing import Generator
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from openscan_firmware.controllers.services.projects import ProjectManager, get_project_manager
from openscan_firmware.main import app
from openscan_firmware.models.project import Project
from openscan_firmware.models.task import Task
from openscan_firmware.config.scan import ScanSetting


@pytest.fixture(scope="function")
def project_manager(monkeypatch: pytest.MonkeyPatch, tmp_path_factory) -> Generator[ProjectManager, None, None]:
    """
    Create a ProjectManager instance with a temporary directory for projects.
    """
    temp_dir = tmp_path_factory.mktemp("projects_api")
    pm = ProjectManager(path=temp_dir)

    module_path = "openscan_firmware.routers.v0_6.projects"
    next_module_path = "openscan_firmware.routers.next.projects"

    monkeypatch.setattr(
        "openscan_firmware.controllers.services.projects.get_project_manager",
        lambda path=None: pm,
        raising=False,
    )
    monkeypatch.setattr(
        module_path + ".get_project_manager",
        lambda: pm,
        raising=False,
    )
    monkeypatch.setattr(
        next_module_path + ".get_project_manager",
        lambda: pm,
        raising=False,
    )

    yield pm

    shutil.rmtree(temp_dir, ignore_errors=True)


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


def test_get_project_thumbnail_missing(client: TestClient, test_project: Project):
    response = client.get(f"/next/projects/{test_project.name}/thumbnail")
    assert response.status_code == 404


def test_get_project_thumbnail_success(client: TestClient, test_project: Project):
    thumbnail_path = Path(test_project.path) / "thumbnail.jpg"
    thumbnail_path.write_bytes(b"fake-jpeg")

    response = client.get(f"/next/projects/{test_project.name}/thumbnail")
    assert response.status_code == 200
    assert response.headers.get("content-type") == "image/jpeg"
    assert response.content == b"fake-jpeg"


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
    project_path = os.path.join(project_manager._path, project_name)
    assert os.path.isdir(project_path)
    assert os.path.isfile(os.path.join(project_path, "openscan_project.json"))

    client.delete(f"/latest/projects/{project_name}")


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


def test_download_project_zip_streaming_headers_without_content_length(
    client: TestClient,
    project_manager: ProjectManager,
    monkeypatch: pytest.MonkeyPatch,
):
    """Ensure the project ZIP endpoint streams without setting Content-Length."""

    class FakeZipStream:
        last_modified = None

        def __init__(self, *_, **__):
            self.add_calls: list[tuple[str, str]] = []

        @classmethod
        def from_path(cls, path: str):
            instance = cls()
            instance.source_path = path
            return instance

        def add(self, data: str, arcname: str) -> None:
            self.add_calls.append((data, arcname))

        def __iter__(self):
            yield b"zip-data"

        def __len__(self) -> int:  # pragma: no cover - should not be invoked
            raise AssertionError("len() must not be called for streaming ZIPs")

    fake_module = types.SimpleNamespace(ZipStream=FakeZipStream)
    monkeypatch.setitem(sys.modules, "zipstream", fake_module)

    project_name = f"zip-stream-test-project-{uuid.uuid4().hex[:8]}"
    create_response = client.post(f"/latest/projects/{project_name}")
    assert create_response.status_code == 200

    response = client.get(f"/latest/projects/{project_name}/zip")

    assert response.status_code == 200
    assert response.headers["Content-Disposition"] == f"attachment; filename={project_name}.zip"
    assert "Content-Length" not in response.headers


def test_download_scans_zip_streaming_handles_large_virtual_size(
    client: TestClient,
    project_manager: ProjectManager,
    monkeypatch: pytest.MonkeyPatch,
):
    """Ensure scan ZIP streaming skips Content-Length even for huge virtual sizes."""

    class FakeLargeZipStream:
        last_modified = datetime(2025, 1, 1, 12, 0, 0)

        def __init__(self, *, sized: bool = False, **_):
            assert sized is True
            self.comment: str | None = None
            self.added_paths: list[tuple[str, str]] = []

        @classmethod
        def from_path(cls, *_: str):
            raise AssertionError("from_path should not be used in scans ZIP test")

        def add(self, data: str, arcname: str) -> None:
            # Collect metadata inclusion
            pass

        def add_path(self, path: str, arcname: str) -> None:
            self.added_paths.append((path, arcname))

        def __iter__(self):
            yield b"large-zip-chunk"

        def __len__(self) -> int:  # pragma: no cover - should not be invoked
            raise OverflowError("Simulated overflow for >2GB zip size")

    fake_module = types.SimpleNamespace(ZipStream=FakeLargeZipStream)
    monkeypatch.setitem(sys.modules, "zipstream", fake_module)

    project_name = f"zip-stream-large-virtual-{uuid.uuid4().hex[:8]}"
    create_response = client.post(f"/latest/projects/{project_name}")
    assert create_response.status_code == 200

    response = client.get(f"/latest/projects/{project_name}/scans/zip")

    assert response.status_code == 200
    assert response.headers["Content-Disposition"].startswith(f"attachment; filename={project_name}")
    assert response.headers["Last-Modified"] == str(FakeLargeZipStream.last_modified)
    assert "Content-Length" not in response.headers