import asyncio
from types import SimpleNamespace
from pathlib import Path

import pytest

from openscan.controllers.services.cloud import CloudServiceError
from openscan.controllers.services.tasks.core.cloud_task import CloudDownloadTask
from openscan.models.task import Task


@pytest.fixture(autouse=True)
def _mock_retry_delay(monkeypatch):
    """Speed up retry loops during tests."""

    monkeypatch.setattr(
        "openscan.controllers.services.tasks.core.cloud_task._DOWNLOAD_RETRY_DELAY_SECONDS",
        0,
    )


def _prepare_environment(monkeypatch, project_manager, *, remote_name="demo-remote.zip"):
    project = project_manager.add_project("demo")
    project.cloud_project_name = remote_name
    monkeypatch.setattr(
        "openscan.controllers.services.tasks.core.cloud_task._require_cloud_settings",
        lambda: SimpleNamespace(),
    )
    monkeypatch.setattr(
        "openscan.controllers.services.tasks.core.cloud_task.get_project_manager",
        lambda: project_manager,
    )
    return project


@pytest.mark.asyncio
async def test_cloud_download_task_success(monkeypatch, project_manager, tmp_path):
    project = _prepare_environment(monkeypatch, project_manager)

    responses = []

    def fake_get_project_info(name: str, token=None):
        responses.append(name)
        return {"dlink": "https://download/link", "status": "finished"}

    archive_path = tmp_path / "archive.zip"
    archive_path.write_bytes(b"zip-bytes")
    expected_size = archive_path.stat().st_size

    async def fake_download(self, dlink: str):  # noqa: ANN001
        assert dlink == "https://download/link"
        return archive_path, archive_path.stat().st_size, archive_path.stat().st_size

    download_calls: list[tuple[str, str]] = []

    def fake_add_download(name: str, path: str):
        download_calls.append((name, path))
        project.downloaded = True
        return project

    monkeypatch.setattr(
        "openscan.controllers.services.tasks.core.cloud_task.get_project_info",
        fake_get_project_info,
    )
    monkeypatch.setattr(
        "openscan.controllers.services.tasks.core.cloud_task.CloudDownloadTask._download_archive",
        fake_download,
    )
    monkeypatch.setattr(project_manager, "add_download", fake_add_download, raising=False)

    task_model = Task(name="cloud_download_task", task_type="cloud_download_task")
    task_instance = CloudDownloadTask(task_model)

    result = await task_instance.run("demo")

    assert responses == ["demo-remote.zip"]
    assert download_calls[0][0] == "demo"
    assert Path(download_calls[0][1]) == archive_path
    assert result.project == "demo-remote.zip"
    assert result.bytes_downloaded == expected_size
    assert project.downloaded is True
    assert task_instance._task_model.progress.current == 1
    assert task_instance._task_model.progress.message == "Download completed"
    assert not archive_path.exists()


@pytest.mark.asyncio
async def test_cloud_download_task_missing_project(monkeypatch, project_manager):
    project_manager.add_project("demo")

    monkeypatch.setattr(
        "openscan.controllers.services.tasks.core.cloud_task._require_cloud_settings",
        lambda: SimpleNamespace(),
    )
    monkeypatch.setattr(
        "openscan.controllers.services.tasks.core.cloud_task.get_project_manager",
        lambda: project_manager,
    )

    task_model = Task(name="cloud_download_task", task_type="cloud_download_task")
    task_instance = CloudDownloadTask(task_model)

    with pytest.raises(CloudServiceError):
        await task_instance.run("unknown")


@pytest.mark.asyncio
async def test_cloud_download_task_no_download_link(monkeypatch, project_manager):
    project = _prepare_environment(monkeypatch, project_manager)

    attempts = 0

    def fake_get_project_info(name: str, token=None):
        nonlocal attempts
        attempts += 1
        return {}

    monkeypatch.setattr(
        "openscan.controllers.services.tasks.core.cloud_task.get_project_info",
        fake_get_project_info,
    )

    task_model = Task(name="cloud_download_task", task_type="cloud_download_task")
    task_instance = CloudDownloadTask(task_model)

    with pytest.raises(CloudServiceError, match="not ready"):
        await task_instance.run(project.name)

    assert attempts == 3


@pytest.mark.asyncio
async def test_cloud_download_task_cancel(monkeypatch, project_manager, tmp_path):
    project = _prepare_environment(monkeypatch, project_manager)

    def fake_get_project_info(name: str, token=None):
        return {"dlink": "https://download/link", "status": "finished"}

    archive_path = tmp_path / "archive.zip"
    archive_path.write_bytes(b"zip-bytes")

    async def slow_download(self, dlink: str):  # noqa: ANN001
        await asyncio.sleep(0.05)
        if self.is_cancelled():
            raise CloudServiceError("Download cancelled")
        return archive_path, archive_path.stat().st_size, archive_path.stat().st_size

    monkeypatch.setattr(
        "openscan.controllers.services.tasks.core.cloud_task.get_project_info",
        fake_get_project_info,
    )
    monkeypatch.setattr(
        "openscan.controllers.services.tasks.core.cloud_task.CloudDownloadTask._download_archive",
        slow_download,
    )

    task_model = Task(name="cloud_download_task", task_type="cloud_download_task")
    task_instance = CloudDownloadTask(task_model)

    run_task = asyncio.create_task(task_instance.run(project.name))
    await asyncio.sleep(0.01)
    task_instance.cancel()

    with pytest.raises(CloudServiceError, match="Download cancelled"):
        await run_task

    assert project.downloaded is False
    assert task_instance._task_model.result is None
