from types import SimpleNamespace

import pytest

from openscan_firmware.config.cloud import CloudSettings, mask_secret
from openscan_firmware.controllers.services.cloud import (
    CloudServiceError,
    _cloud_request,
    download_project,
    upload_project,
    logger as cloud_logger,
)
from openscan_firmware.controllers.services.projects import ProjectManager
from openscan_firmware.models.project import Project
from openscan_firmware.models.task import Task, TaskStatus


@pytest.fixture
def project_manager(tmp_path) -> ProjectManager:
    manager = ProjectManager(path=tmp_path)
    project = Project(
        name="demo",
        path=str(tmp_path / "demo"),
        created="2024-01-01T00:00:00",
        scans={},
        uploaded=False,
    )
    manager._projects[project.name] = project
    return manager


@pytest.fixture
def task_manager(monkeypatch):
    class StubTaskManager:
        def __init__(self):
            self.tasks: list[Task] = []

        def get_all_tasks_info(self):
            return list(self.tasks)

        async def create_and_run_task(self, task_name, project_name, **kwargs):
            task = Task(
                name=task_name,
                task_type=task_name,
                run_args=(project_name,),
                run_kwargs=kwargs,
                status=TaskStatus.PENDING,
            )
            self.tasks.append(task)
            return task

        def add_task(self, task: Task):
            self.tasks.append(task)

    manager = StubTaskManager()

    monkeypatch.setattr(
        "openscan_firmware.controllers.services.cloud.get_task_manager",
        lambda: manager,
    )

    return manager


@pytest.fixture(autouse=True)
def mock_cloud_settings(monkeypatch):
    monkeypatch.setattr(
        "openscan_firmware.controllers.services.cloud._require_cloud_settings",
        lambda: SimpleNamespace(),
    )


@pytest.mark.asyncio
async def test_upload_project_rejects_uploaded_project(monkeypatch, project_manager, task_manager):
    project = project_manager.get_project_by_name("demo")
    project.uploaded = True
    project.cloud_project_name = "demo-remote.zip"

    monkeypatch.setattr(
        "openscan_firmware.controllers.services.cloud.get_project_manager",
        lambda: project_manager,
    )

    with pytest.raises(CloudServiceError, match="already uploaded"):
        await upload_project("demo")


@pytest.mark.asyncio
async def test_upload_project_rejects_running_task(monkeypatch, project_manager, task_manager):
    task = Task(
        name="cloud_upload_task",
        task_type="cloud_upload_task",
        status=TaskStatus.RUNNING,
        run_args=("demo",),
    )
    task_manager.add_task(task)

    monkeypatch.setattr(
        "openscan_firmware.controllers.services.cloud.get_project_manager",
        lambda: project_manager,
    )

    with pytest.raises(CloudServiceError, match="already in progress"):
        await upload_project("demo")


@pytest.mark.asyncio
async def test_upload_project_starts_when_not_blocked(monkeypatch, project_manager, task_manager):
    project_manager.get_project_by_name("demo").cloud_project_name = None

    created_task = Task(name="cloud_upload_task", task_type="cloud_upload_task")

    async def fake_create_and_run(task_name, project_name, **kwargs):
        task_manager.add_task(created_task)
        return created_task

    monkeypatch.setattr(
        "openscan_firmware.controllers.services.cloud.get_project_manager",
        lambda: project_manager,
    )
    monkeypatch.setattr(
        "openscan_firmware.controllers.services.cloud.get_task_manager",
        lambda: task_manager,
    )
    monkeypatch.setattr(task_manager, "create_and_run_task", fake_create_and_run)

    task = await upload_project("demo")
    assert task is created_task


@pytest.mark.asyncio
async def test_download_project_requires_remote(monkeypatch, project_manager, task_manager):
    project = project_manager.get_project_by_name("demo")
    project.cloud_project_name = None

    monkeypatch.setattr(
        "openscan_firmware.controllers.services.cloud.get_project_manager",
        lambda: project_manager,
    )

    with pytest.raises(CloudServiceError, match="Upload the project"):
        await download_project("demo")


@pytest.mark.asyncio
async def test_download_project_rejects_running_task(monkeypatch, project_manager, task_manager):
    project = project_manager.get_project_by_name("demo")
    project.cloud_project_name = "demo-remote.zip"

    task = Task(
        name="cloud_download_task",
        task_type="cloud_download_task",
        status=TaskStatus.RUNNING,
        run_args=("demo",),
    )
    task_manager.add_task(task)

    monkeypatch.setattr(
        "openscan_firmware.controllers.services.cloud.get_project_manager",
        lambda: project_manager,
    )

    with pytest.raises(CloudServiceError, match="already in progress"):
        await download_project("demo")


@pytest.mark.asyncio
async def test_download_project_starts(monkeypatch, project_manager, task_manager):
    project = project_manager.get_project_by_name("demo")
    project.cloud_project_name = "demo-remote.zip"

    created_task = Task(name="cloud_download_task", task_type="cloud_download_task")

    async def fake_create_and_run(task_name, project_name, **kwargs):
        assert kwargs["remote_project"] == "demo-remote.zip"
        task_manager.add_task(created_task)
        return created_task

    monkeypatch.setattr(
        "openscan_firmware.controllers.services.cloud.get_project_manager",
        lambda: project_manager,
    )
    monkeypatch.setattr(
        "openscan_firmware.controllers.services.cloud.get_task_manager",
        lambda: task_manager,
    )
    monkeypatch.setattr(task_manager, "create_and_run_task", fake_create_and_run)

    task = await download_project("demo")
    assert task is created_task


def test_cloud_request_masks_token_in_logs(monkeypatch, caplog):
    settings = CloudSettings(
        user="api-user",
        password="secret",
        token="token-secret",
        host="http://example.com",
        split_size=1024,
    )

    monkeypatch.setattr(
        "openscan_firmware.controllers.services.cloud._require_cloud_settings",
        lambda: settings,
    )

    def fake_request(method, url, auth, params, timeout):
        assert params["token"] == settings.token
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(
        "openscan_firmware.controllers.services.cloud.requests.request",
        fake_request,
    )

    with caplog.at_level("DEBUG", logger=cloud_logger.name):
        _cloud_request("get", "status")

    log_text = caplog.text
    assert settings.token not in log_text
    assert mask_secret(settings.token) in log_text
