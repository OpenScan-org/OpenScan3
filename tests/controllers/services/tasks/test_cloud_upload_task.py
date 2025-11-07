import asyncio
import io
import logging
import time
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Callable
from zipfile import ZipFile

import pytest
from pytest import MonkeyPatch

from openscan.controllers.services.cloud import (
    CloudServiceError,
    _build_project_archive,
    _count_project_photos,
)
from openscan.controllers.services.tasks.core.cloud_task import CloudUploadTask
from openscan.models.project import Project
from openscan.models.task import Task


def _patch_cloud_dependencies(
    monkeypatch: MonkeyPatch,
    project_manager,
    *,
    split_size: int = 100,
    archive_size: int = 200,
    photo_count: int = 5,
    upload_links: list[str] | None = None,
    chunk_payloads: list[bytes] | None = None,
    remote_project: str = "remote-project.zip",
    upload_side_effect: Callable[[bytes, str], None] | None = None,
):
    """Patch helper to simulate cloud upload dependencies."""

    settings = SimpleNamespace(split_size=split_size)
    monkeypatch.setattr(
        "openscan.controllers.services.tasks.core.cloud_task._require_cloud_settings",
        lambda: settings,
    )
    monkeypatch.setattr(
        "openscan.controllers.services.tasks.core.cloud_task.get_project_manager",
        lambda: project_manager,
    )

    monkeypatch.setattr(
        "openscan.controllers.services.tasks.core.cloud_task._generate_remote_project_name",
        lambda _name: remote_project,
    )

    fake_archive = io.BytesIO(b"archive-bytes")
    monkeypatch.setattr(
        "openscan.controllers.services.tasks.core.cloud_task._build_project_archive",
        lambda _project: (fake_archive, archive_size),
    )
    monkeypatch.setattr(
        "openscan.controllers.services.tasks.core.cloud_task._count_project_photos",
        lambda _project: photo_count,
    )

    links = upload_links or ["https://upload/1", "https://upload/2"]
    create_calls: list[dict[str, Any]] = []

    def fake_create_project(*args: Any, **kwargs: Any) -> dict[str, Any]:
        create_calls.append({"args": args, "kwargs": kwargs})
        return {"ulink": links}

    monkeypatch.setattr(
        "openscan.controllers.services.tasks.core.cloud_task._create_project",
        fake_create_project,
    )

    start_calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(
        "openscan.controllers.services.tasks.core.cloud_task._start_project",
        lambda *args: start_calls.append(args) or {"status": "started"},
    )

    payloads = chunk_payloads or [b"part1", b"part2"]
    parts = [io.BytesIO(data) for data in payloads]
    monkeypatch.setattr(
        "openscan.controllers.services.tasks.core.cloud_task._iter_chunks",
        lambda *_: iter(parts),
    )

    uploads: list[tuple[bytes, str]] = []

    def fake_upload_file(chunk: io.BytesIO, link: str) -> None:
        data = chunk.read()
        uploads.append((data, link))
        if upload_side_effect:
            upload_side_effect(data, link)

    monkeypatch.setattr(
        "openscan.controllers.services.tasks.core.cloud_task._upload_file",
        fake_upload_file,
    )

    mark_calls: list[tuple[str, bool, str | None]] = []

    def fake_mark_uploaded(name: str, uploaded: bool = True, cloud_project_name: str | None = None):
        mark_calls.append((name, uploaded, cloud_project_name))
        project = project_manager.get_project_by_name(name)
        if project:
            project.uploaded = uploaded
            project.cloud_project_name = cloud_project_name
        return project

    monkeypatch.setattr(project_manager, "mark_uploaded", fake_mark_uploaded, raising=False)

    return SimpleNamespace(
        uploads=uploads,
        create_calls=create_calls,
        start_calls=start_calls,
        upload_links=links,
        remote_project=remote_project,
        mark_calls=mark_calls,
    )


@pytest.mark.asyncio
async def test_cloud_upload_task_success(monkeypatch: MonkeyPatch, project_manager):
    """Uploading a project streams all parts and updates result/progress."""

    project = project_manager.add_project("demo")
    patched = _patch_cloud_dependencies(monkeypatch, project_manager, remote_project="demo-remote.zip")

    task_model = Task(
        name="cloud_upload_task",
        task_type="cloud_upload_task",
        is_exclusive=False,
        is_blocking=False,
    )
    task_instance = CloudUploadTask(task_model)

    result = await task_instance.run(project.name, token="override-token")

    assert result.parts_uploaded == 2
    assert patched.uploads == [
        (b"part1", patched.upload_links[0]),
        (b"part2", patched.upload_links[1]),
    ]
    assert task_instance._task_model.progress.current == 2
    assert task_instance._task_model.progress.total == 2
    assert task_instance._task_model.progress.message == "Upload completed"
    assert patched.create_calls[0]["kwargs"]["token"] == "override-token"
    assert patched.start_calls[0][0] == patched.remote_project
    assert task_instance._task_model.result.project == patched.remote_project
    assert patched.mark_calls == [(project.name, True, patched.remote_project)]
    assert project_manager.get_project_by_name(project.name).uploaded is True
    assert project_manager.get_project_by_name(project.name).cloud_project_name == patched.remote_project


@pytest.mark.asyncio
async def test_cloud_upload_task_missing_project(monkeypatch: MonkeyPatch, project_manager):
    """An error is raised when the project does not exist."""

    _patch_cloud_dependencies(monkeypatch, project_manager)

    task_model = Task(
        name="cloud_upload_task",
        task_type="cloud_upload_task",
    )
    task_instance = CloudUploadTask(task_model)

    with pytest.raises(CloudServiceError):
        await task_instance.run("unknown")


@pytest.mark.asyncio
async def test_cloud_upload_task_pause_and_resume(monkeypatch: MonkeyPatch, project_manager):
    project = project_manager.add_project("demo-pause")
    patched = _patch_cloud_dependencies(
        monkeypatch,
        project_manager,
        remote_project="pause-remote.zip",
    )

    task_model = Task(
        name="cloud_upload_task",
        task_type="cloud_upload_task",
        is_exclusive=False,
        is_blocking=False,
    )
    task_instance = CloudUploadTask(task_model)
    task_instance.pause()

    run_task = asyncio.create_task(task_instance.run(project.name))

    await asyncio.sleep(0.05)
    assert task_instance._task_model.progress.current == 0
    assert task_instance._task_model.result is None

    task_instance.resume()
    result = await run_task

    assert result.project == patched.remote_project
    assert task_instance._task_model.progress.current == 2
    assert patched.mark_calls == [(project.name, True, patched.remote_project)]


@pytest.mark.asyncio
async def test_cloud_upload_task_cancel(monkeypatch: MonkeyPatch, project_manager):
    project = project_manager.add_project("demo-cancel")

    def slow_upload(_data: bytes, _link: str) -> None:
        time.sleep(0.05)

    patched = _patch_cloud_dependencies(
        monkeypatch,
        project_manager,
        remote_project="cancel-remote.zip",
        split_size=50,
        chunk_payloads=[b"part1", b"part2", b"part3", b"part4"],
        upload_side_effect=slow_upload,
    )

    task_model = Task(name="cloud_upload_task", task_type="cloud_upload_task")
    task_instance = CloudUploadTask(task_model)

    run_task = asyncio.create_task(task_instance.run(project.name))
    await asyncio.sleep(0.12)
    task_instance.cancel()

    with pytest.raises(CloudServiceError):
        await run_task

    assert task_instance._task_model.result is None
    assert patched.mark_calls == []
    assert project_manager.get_project_by_name(project.name).uploaded is False


def test_build_project_archive_filters_non_jpeg(tmp_path, caplog):
    project_path = tmp_path / "project"
    scan1 = project_path / "scan01"
    scan2 = project_path / "scan02"
    scan1.mkdir(parents=True)
    scan2.mkdir(parents=True)

    (scan1 / "img1.jpg").write_bytes(b"jpg1")
    (scan1 / "img2.png").write_bytes(b"png")
    (scan2 / "img3.JPEG").write_bytes(b"jpeg")
    (scan2 / "depth.npy").write_bytes(b"npy")

    project = Project(
        name="demo",
        path=str(project_path),
        created=datetime.now(),
        scans={},
    )

    with caplog.at_level(logging.WARNING):
        archive, size = _build_project_archive(project)

    try:
        assert size > 0
        with ZipFile(archive, "r") as zipf:
            assert sorted(zipf.namelist()) == ["img1.jpg", "img3.JPEG"]
    finally:
        archive.close()

    assert _count_project_photos(project) == 2
    warnings = [r for r in caplog.records if "Skipping unsupported photo" in r.message]
    assert len(warnings) == 2
