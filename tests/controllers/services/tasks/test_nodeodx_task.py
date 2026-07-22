from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from openscan_firmware.controllers.services.tasks.task_manager import TaskManager
from openscan_firmware.models.project import Project
from openscan_firmware.models.task import Task
from openscan_firmware.tasks.community.nodeodx_task import NodeodxReconstructionTask


class FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self.payload


@pytest.fixture
def project(tmp_path: Path) -> Project:
    (tmp_path / "scan01").mkdir()
    (tmp_path / "scan01" / "image01.jpg").write_bytes(b"first")
    (tmp_path / "scan01" / "image02.jpg").write_bytes(b"second")
    return Project(name="demo", path=str(tmp_path), created=datetime.now(), scans={})


@pytest.mark.asyncio
async def test_nodeodx_task_uploads_images_and_waits_for_result(monkeypatch, project):
    manager = type("ProjectManager", (), {"get_project_by_name": lambda self, name: project})()
    monkeypatch.setattr(
        "openscan_firmware.tasks.community.nodeodx_task.get_project_manager",
        lambda: manager,
    )

    uploaded_names = []

    def fake_post(url, data, files, timeout):
        uploaded_names.extend(file[1][0] for file in files)
        assert url == "http://node:3000/task/new"
        assert data == {"name": "demo"}
        assert timeout == 300
        return FakeResponse({"uuid": "remote-id"})

    responses = iter(
        [
            {"status": {"code": 20}, "progress": 25},
            {"status": {"code": 40}, "progress": 100},
        ]
    )
    monkeypatch.setattr(
        "openscan_firmware.tasks.community.nodeodx_task.requests.post",
        fake_post,
    )
    monkeypatch.setattr(
        "openscan_firmware.tasks.community.nodeodx_task.requests.get",
        lambda *args, **kwargs: FakeResponse(next(responses)),
    )

    model = Task(name="nodeodx_reconstruction_task", task_type="nodeodx_reconstruction_task")
    progress = [
        update
        async for update in NodeodxReconstructionTask(model).run(
            "demo", server_url="http://node:3000/", poll_interval=0
        )
    ]

    assert uploaded_names == ["image01.jpg", "image02.jpg"]
    assert [update.current for update in progress] == [0, 25, 100]
    assert model.result == {
        "uuid": "remote-id",
        "download_url": "http://node:3000/task/remote-id/download/all.zip",
    }


@pytest.mark.asyncio
async def test_nodeodx_task_rejects_projects_with_too_few_images(monkeypatch, tmp_path):
    (tmp_path / "only.jpg").write_bytes(b"one")
    project = Project(name="small", path=str(tmp_path), created=datetime.now(), scans={})
    manager = type("ProjectManager", (), {"get_project_by_name": lambda self, name: project})()
    monkeypatch.setattr(
        "openscan_firmware.tasks.community.nodeodx_task.get_project_manager",
        lambda: manager,
    )

    model = Task(name="nodeodx_reconstruction_task", task_type="nodeodx_reconstruction_task")
    task = NodeodxReconstructionTask(model)

    with pytest.raises(ValueError, match="at least two"):
        async for _ in task.run("small"):
            pass


def test_nodeodx_task_is_registered_by_autodiscovery():
    TaskManager._instance = None
    task_manager = TaskManager()

    registered = task_manager.autodiscover_tasks(
        namespaces=["openscan_firmware.tasks.community"]
    )

    assert "nodeodx_reconstruction_task" in registered
