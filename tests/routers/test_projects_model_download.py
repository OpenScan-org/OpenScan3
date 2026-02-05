"""Tests for the project model download endpoint."""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import ClassVar, Generator, Optional

import pytest
from fastapi.testclient import TestClient

from openscan_firmware.controllers.services.projects import ProjectManager, get_project_manager
from openscan_firmware.main import app


@pytest.fixture(scope="function")
def project_manager(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[ProjectManager, None, None]:
    temp_dir = tmp_path_factory.mktemp("projects_model_zip")
    manager = ProjectManager(path=temp_dir)

    monkeypatch.setattr(
        "openscan_firmware.controllers.services.projects.get_project_manager",
        lambda path=None: manager,
        raising=False,
    )
    monkeypatch.setattr(
        "openscan_firmware.routers.v0_6.projects.get_project_manager",
        lambda: manager,
        raising=False,
    )
    monkeypatch.setattr(
        "openscan_firmware.routers.next.projects.get_project_manager",
        lambda: manager,
        raising=False,
    )

    yield manager


@pytest.fixture(scope="function")
def client(project_manager: ProjectManager) -> Generator[TestClient, None, None]:
    app.dependency_overrides[get_project_manager] = lambda: project_manager
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture(scope="function")
def test_project(project_manager: ProjectManager):
    return project_manager.add_project("model-zip-project")


def test_download_model_zip_streams_existing_model(
    client: TestClient,
    test_project: ProjectManager,
    monkeypatch: pytest.MonkeyPatch,
):
    class FakeModelZipStream:
        last_modified = None
        latest: ClassVar[Optional["FakeModelZipStream"]] = None

        def __init__(self, *, sized: bool = False):
            assert sized is True
            self.comment: str | None = None
            self.added_paths: list[tuple[str, str]] = []
            self.added_metadata: list[tuple[str, str]] = []
            type(self).latest = self

        def add_path(self, path: str, arcname: str) -> None:
            self.added_paths.append((path, arcname))

        def add(self, data: str, arcname: str) -> None:
            self.added_metadata.append((data, arcname))

        def __iter__(self):
            yield b"model-zip"

    fake_module = types.SimpleNamespace(ZipStream=FakeModelZipStream)
    monkeypatch.setitem(sys.modules, "zipstream", fake_module)

    model_dir = Path(test_project.path) / "model"
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / "mesh.obj").write_text("o fake", encoding="utf-8")

    response = client.get(f"/next/projects/{test_project.name}/model/zip")

    assert response.status_code == 200
    assert response.headers["Content-Disposition"] == f"attachment; filename={test_project.name}_model.zip"
    assert "Content-Length" not in response.headers

    stream = FakeModelZipStream.latest
    assert stream is not None
    assert (str(model_dir), "model") in stream.added_paths
    assert any(arcname == "project_metadata.json" for _, arcname in stream.added_metadata)


def test_download_model_zip_missing_directory_returns_404(
    client: TestClient,
    test_project: ProjectManager,
    monkeypatch: pytest.MonkeyPatch,
):
    class DummyZipStream:
        def __init__(self, *_, **__):
            raise AssertionError("ZipStream should not be constructed when model is missing")

    monkeypatch.setitem(sys.modules, "zipstream", types.SimpleNamespace(ZipStream=DummyZipStream))

    response = client.get(f"/next/projects/{test_project.name}/model/zip")

    assert response.status_code == 404
    assert response.json()["detail"].startswith("No reconstructed model")
