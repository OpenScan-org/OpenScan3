"""
Tests for the projects API endpoints.
"""
import asyncio
import io
import os
import shutil
import sys
import tempfile
import time
import types
import uuid
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Generator
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from httpx import AsyncClient, ASGITransport

from openscan_firmware.controllers.services.projects import ProjectManager, get_project_manager
from openscan_firmware.controllers.services.tasks import task_manager as task_manager_module
from openscan_firmware.controllers.services.tasks.core.cloud_task import CloudUploadTask
from openscan_firmware.controllers.services.tasks.task_manager import TaskManager
from openscan_firmware.main import app, LATEST
from openscan_firmware.models.project import Project
from openscan_firmware.models.scan import Scan
from openscan_firmware.models.task import Task, TaskStatus
from openscan_firmware.config.scan import ScanSetting
from openscan_firmware.config.camera import CameraSettings


@pytest.fixture(scope="function")
def project_manager(monkeypatch: pytest.MonkeyPatch, tmp_path_factory) -> Generator[ProjectManager, None, None]:
    """
    Create a ProjectManager instance with a temporary directory for projects.
    """
    temp_dir = tmp_path_factory.mktemp("projects_api")
    pm = ProjectManager(path=temp_dir)

    module_path_v0_8 = "openscan_firmware.routers.v0_8.projects"
    latest_module_path = f"openscan_firmware.routers.v{LATEST.replace('.', '_')}.projects"
    next_module_path = "openscan_firmware.routers.next.projects"

    monkeypatch.setattr(
        "openscan_firmware.controllers.services.projects.get_project_manager",
        lambda path=None: pm,
        raising=False,
    )
    monkeypatch.setattr(
        "openscan_firmware.controllers.device.get_project_manager",
        lambda: pm,
        raising=False,
    )
    monkeypatch.setattr(
        "openscan_firmware.controllers.device._detect_cameras",
        lambda: {},
        raising=False,
    )
    monkeypatch.setattr(
        "openscan_firmware.main.is_network_ready_for_qr_scan",
        lambda: True,
        raising=False,
    )
    for module_path in (module_path_v0_8, latest_module_path, next_module_path):
        monkeypatch.setattr(
            module_path + ".get_project_manager",
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


def _prepare_cloud_upload_dependencies(monkeypatch: pytest.MonkeyPatch, project_manager: ProjectManager):
    fake_settings = SimpleNamespace(split_size=50)
    monkeypatch.setattr(
        "openscan_firmware.controllers.services.cloud._require_cloud_settings",
        lambda: SimpleNamespace(token="dummy"),
    )
    monkeypatch.setattr(
        "openscan_firmware.controllers.services.tasks.core.cloud_task._require_cloud_settings",
        lambda: fake_settings,
    )
    monkeypatch.setattr(
        "openscan_firmware.controllers.services.tasks.core.cloud_task.get_project_manager",
        lambda: project_manager,
    )
    monkeypatch.setattr(
        "openscan_firmware.controllers.services.cloud.get_project_manager",
        lambda: project_manager,
    )
    fake_archive = io.BytesIO(b"a" * 120)
    monkeypatch.setattr(
        "openscan_firmware.controllers.services.tasks.core.cloud_task._build_project_archive",
        lambda _project: (fake_archive, 120),
    )
    monkeypatch.setattr(
        "openscan_firmware.controllers.services.tasks.core.cloud_task._count_project_photos",
        lambda _project: 2,
    )

    monkeypatch.setattr(
        "openscan_firmware.controllers.services.tasks.core.cloud_task._generate_remote_project_name",
        lambda name: f"{name}-remote.zip",
    )

    def fake_create_project(*_, **__):
        return {"ulink": ["https://upload/1", "https://upload/2", "https://upload/3"]}

    monkeypatch.setattr(
        "openscan_firmware.controllers.services.tasks.core.cloud_task._create_project",
        fake_create_project,
    )

    payloads = [b"a" * 40, b"b" * 40, b"c" * 40]
    monkeypatch.setattr(
        "openscan_firmware.controllers.services.tasks.core.cloud_task._iter_chunks",
        lambda *_: iter([io.BytesIO(p) for p in payloads]),
    )

    def fake_upload_file(chunk: io.BytesIO, *_args, progress_callback=None, **__):
        chunk.seek(0)
        data = chunk.read()
        if progress_callback:
            step = 10
            for offset in range(0, len(data), step):
                progress_callback(min(step, len(data) - offset))

    monkeypatch.setattr(
        "openscan_firmware.controllers.services.tasks.core.cloud_task._upload_file",
        fake_upload_file,
    )

    monkeypatch.setattr(
        "openscan_firmware.controllers.services.tasks.core.cloud_task._start_project",
        lambda *_, **__: {"status": "started"},
    )


@pytest.mark.asyncio
async def test_upload_endpoint_returns_task_with_streaming_progress(
    monkeypatch: pytest.MonkeyPatch,
    project_manager: ProjectManager,
    tmp_path_factory,
):
    project = project_manager.add_project("api-upload")

    _prepare_cloud_upload_dependencies(monkeypatch, project_manager)

    storage_dir = tmp_path_factory.mktemp("task_storage")
    monkeypatch.setattr(
        task_manager_module,
        "TASKS_STORAGE_PATH",
        storage_dir,
        raising=False,
    )
    TaskManager._instance = None
    tm = TaskManager()
    tm.register_task("cloud_upload_task", CloudUploadTask)

    monkeypatch.setattr(
        "openscan_firmware.controllers.services.cloud.get_task_manager",
        lambda: tm,
    )

    app.dependency_overrides[get_project_manager] = lambda: project_manager
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        response = await async_client.post(f"/next/projects/{project.name}/upload")

    assert response.status_code == 200
    body = response.json()
    task_id = body["id"]

    async def _collect_progress():
        progress_values = []
        for _ in range(50):
            task = tm.get_task_info(task_id)
            if task.progress.current:
                progress_values.append(task.progress.current)
            if task.status == TaskStatus.COMPLETED:
                break
            await asyncio.sleep(0.01)
        return progress_values, tm.get_task_info(task_id)

    progress_values, final_task = await _collect_progress()
    assert progress_values, "expected progress updates from API-triggered upload"
    assert progress_values == sorted(progress_values)
    assert final_task.progress.current == final_task.progress.total
    assert final_task.progress.total == 120
    assert final_task.status == TaskStatus.COMPLETED

    app.dependency_overrides.clear()

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


def test_download_project_zip_photos_only_prefers_stacked_outputs(
    client: TestClient,
    project_manager: ProjectManager,
    monkeypatch: pytest.MonkeyPatch,
):
    class FakeZipStream:
        latest = None
        last_modified = None

        def __init__(self, *_, **__):
            self.added_paths: list[tuple[str, str]] = []
            self.added_metadata: list[tuple[str, str]] = []
            type(self).latest = self

        @classmethod
        def from_path(cls, *_: str):
            raise AssertionError("from_path should not be used for photos_only downloads")

        def add_path(self, path: str, arcname: str) -> None:
            self.added_paths.append((path, arcname))

        def add(self, data: str, arcname: str) -> None:
            self.added_metadata.append((data, arcname))

        def __iter__(self):
            yield b"zip-data"

    monkeypatch.setitem(sys.modules, "zipstream", types.SimpleNamespace(ZipStream=FakeZipStream))

    project_name = f"zip-pref-stack-{uuid.uuid4().hex[:8]}"
    project = project_manager.add_project(project_name)
    scan = Scan(
        project_name=project.name,
        index=1,
        settings=ScanSetting(),
        camera_settings=CameraSettings(),
    )
    scan.photos = ["scan01_001.jpg"]
    project.scans["scan01"] = scan

    scan_dir = Path(project.path) / "scan01"
    stacked_dir = scan_dir / "stacked"
    scan_dir.mkdir(parents=True, exist_ok=True)
    stacked_dir.mkdir(parents=True, exist_ok=True)
    raw_photo = scan_dir / "scan01_001.jpg"
    stacked_photo = stacked_dir / "stacked_scan01_001.jpg"
    raw_photo.write_bytes(b"raw")
    stacked_photo.write_bytes(b"stacked")

    response = client.get(
        f"/latest/projects/{project_name}/zip",
        params={"photos_only": "true", "prefer_stacked_photos": "true"},
    )

    assert response.status_code == 200
    stream = FakeZipStream.latest
    assert stream is not None
    added_paths = {path for path, _ in stream.added_paths}
    assert str(stacked_photo) in added_paths
    assert str(raw_photo) not in added_paths


def test_download_project_zip_photos_only_excludes_stacked_without_preference(
    client: TestClient,
    project_manager: ProjectManager,
    monkeypatch: pytest.MonkeyPatch,
):
    class FakeZipStream:
        latest = None
        last_modified = None

        def __init__(self, *_, **__):
            self.added_paths: list[tuple[str, str]] = []
            self.added_metadata: list[tuple[str, str]] = []
            type(self).latest = self

        @classmethod
        def from_path(cls, *_: str):
            raise AssertionError("from_path should not be used for photos_only downloads")

        def add_path(self, path: str, arcname: str) -> None:
            self.added_paths.append((path, arcname))

        def add(self, data: str, arcname: str) -> None:
            self.added_metadata.append((data, arcname))

        def __iter__(self):
            yield b"zip-data"

    monkeypatch.setitem(sys.modules, "zipstream", types.SimpleNamespace(ZipStream=FakeZipStream))

    project_name = f"zip-photos-only-raw-{uuid.uuid4().hex[:8]}"
    project = project_manager.add_project(project_name)
    scan = Scan(
        project_name=project.name,
        index=1,
        settings=ScanSetting(),
        camera_settings=CameraSettings(),
    )
    scan.photos = ["scan01_001.jpg", "stacked/stacked_scan01_001.jpg"]
    project.scans["scan01"] = scan

    scan_dir = Path(project.path) / "scan01"
    stacked_dir = scan_dir / "stacked"
    scan_dir.mkdir(parents=True, exist_ok=True)
    stacked_dir.mkdir(parents=True, exist_ok=True)
    raw_photo = scan_dir / "scan01_001.jpg"
    stacked_photo = stacked_dir / "stacked_scan01_001.jpg"
    raw_photo.write_bytes(b"raw")
    stacked_photo.write_bytes(b"stacked")

    response = client.get(
        f"/latest/projects/{project_name}/zip",
        params={"photos_only": "true"},
    )

    assert response.status_code == 200
    stream = FakeZipStream.latest
    assert stream is not None
    added_paths = {path for path, _ in stream.added_paths}
    assert str(raw_photo) in added_paths
    assert str(stacked_photo) not in added_paths


def test_download_scans_zip_prefers_stacked_and_skips_original_photos(
    client: TestClient,
    project_manager: ProjectManager,
    monkeypatch: pytest.MonkeyPatch,
):
    class FakeZipStream:
        latest = None
        last_modified = None

        def __init__(self, *_, **__):
            self.added_paths: list[tuple[str, str]] = []
            self.added_metadata: list[tuple[str, str]] = []
            self.comment: str | None = None
            type(self).latest = self

        def add_path(self, path: str, arcname: str) -> None:
            self.added_paths.append((path, arcname))

        def add(self, data: str, arcname: str) -> None:
            self.added_metadata.append((data, arcname))

        def __iter__(self):
            yield b"zip-data"

    monkeypatch.setitem(sys.modules, "zipstream", types.SimpleNamespace(ZipStream=FakeZipStream))

    project_name = f"scan-zip-pref-stack-{uuid.uuid4().hex[:8]}"
    project = project_manager.add_project(project_name)
    scan = Scan(
        project_name=project.name,
        index=1,
        settings=ScanSetting(),
        camera_settings=CameraSettings(),
    )
    scan.photos = ["scan01_001.jpg"]
    project.scans["scan01"] = scan

    scan_dir = Path(project.path) / "scan01"
    metadata_dir = scan_dir / "metadata"
    stacked_dir = scan_dir / "stacked"
    scan_dir.mkdir(parents=True, exist_ok=True)
    metadata_dir.mkdir(parents=True, exist_ok=True)
    stacked_dir.mkdir(parents=True, exist_ok=True)
    raw_photo = scan_dir / "scan01_001.jpg"
    raw_metadata = metadata_dir / "scan01_001.json"
    stacked_photo = stacked_dir / "stacked_scan01_001.jpg"
    raw_photo.write_bytes(b"raw")
    raw_metadata.write_text("{}", encoding="utf-8")
    stacked_photo.write_bytes(b"stacked")

    response = client.get(
        f"/latest/projects/{project_name}/scans/zip",
        params={"scan_indices": [1], "prefer_stacked_photos": "true"},
    )

    assert response.status_code == 200
    stream = FakeZipStream.latest
    assert stream is not None
    added_paths = {path for path, _ in stream.added_paths}
    assert str(stacked_photo) in added_paths
    assert str(raw_photo) not in added_paths
    assert str(raw_metadata) not in added_paths


def test_get_scan_photo_supports_stacked_relative_path(
    client: TestClient,
    project_manager: ProjectManager,
):
    project_name = f"photo-stacked-{uuid.uuid4().hex[:8]}"
    project = project_manager.add_project(project_name)
    scan = Scan(
        project_name=project.name,
        index=1,
        settings=ScanSetting(),
        camera_settings=CameraSettings(),
    )
    stacked_relpath = "stacked/stacked_scan01_001.jpg"
    scan.photos = [stacked_relpath]
    project.scans["scan01"] = scan

    scan_dir = Path(project.path) / "scan01"
    stacked_path = scan_dir / stacked_relpath
    stacked_path.parent.mkdir(parents=True, exist_ok=True)
    stacked_path.write_bytes(b"stacked")

    response = client.get(
        f"/latest/projects/{project_name}/1/photo",
        params={"filename": stacked_relpath, "file_only": "true"},
    )

    assert response.status_code == 200
    assert response.content == b"stacked"


def test_delete_photos_returns_404_for_missing_scan(
    client: TestClient,
    project_manager: ProjectManager,
):
    project_name = f"delete-missing-scan-{uuid.uuid4().hex[:8]}"
    project_manager.add_project(project_name)

    response = client.delete(
        f"/latest/projects/{project_name}/99/photos",
        params={"photo_filenames": ["scan99_001.jpg"]},
    )

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_delete_photos_returns_400_for_invalid_relative_path(
    client: TestClient,
    project_manager: ProjectManager,
):
    project_name = f"delete-invalid-path-{uuid.uuid4().hex[:8]}"
    project = project_manager.add_project(project_name)
    scan = Scan(
        project_name=project.name,
        index=1,
        settings=ScanSetting(),
        camera_settings=CameraSettings(),
    )
    project.scans["scan01"] = scan

    response = client.delete(
        f"/latest/projects/{project_name}/1/photos",
        params={"photo_filenames": ["../escape.jpg"]},
    )

    assert response.status_code == 400
    assert "invalid photo filename" in response.json()["detail"].lower()
