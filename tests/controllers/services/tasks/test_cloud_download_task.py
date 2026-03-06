import asyncio
from types import SimpleNamespace
from pathlib import Path

import pytest

from openscan_firmware.controllers.services.cloud import CloudServiceError
from openscan_firmware.controllers.services.tasks.core.cloud_task import CloudDownloadTask
from openscan_firmware.models.task import Task


@pytest.fixture(autouse=True)
def _mock_retry_delay(monkeypatch):
    """Speed up retry loops during tests."""

    monkeypatch.setattr(
        "openscan_firmware.controllers.services.tasks.core.cloud_task._DOWNLOAD_RETRY_DELAY_SECONDS",
        0,
    )


def _prepare_environment(monkeypatch, project_manager, *, remote_name="demo-remote.zip"):
    project = project_manager.add_project("demo")
    project.cloud_project_name = remote_name
    monkeypatch.setattr(
        "openscan_firmware.controllers.services.tasks.core.cloud_task._require_cloud_settings",
        lambda: SimpleNamespace(),
    )
    monkeypatch.setattr(
        "openscan_firmware.controllers.services.tasks.core.cloud_task.get_project_manager",
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

    async def fake_stream(self, dlink: str, download_info: dict, stream_result: dict):  # noqa: ANN001
        assert dlink == "https://download/link"
        assert download_info == {"dlink": "https://download/link", "status": "finished"}
        downloaded = archive_path.stat().st_size
        stream_result["path"] = archive_path
        stream_result["bytes_downloaded"] = downloaded
        stream_result["total_bytes"] = downloaded
        yield downloaded, downloaded

    download_calls: list[tuple[str, str]] = []

    def fake_add_download(name: str, path: str):
        download_calls.append((name, path))
        project.downloaded = True
        return project

    monkeypatch.setattr(
        "openscan_firmware.controllers.services.tasks.core.cloud_task.get_project_info",
        fake_get_project_info,
    )
    monkeypatch.setattr(
        "openscan_firmware.controllers.services.tasks.core.cloud_task.CloudDownloadTask._download_archive_stream",
        fake_stream,
    )
    monkeypatch.setattr(project_manager, "add_download", fake_add_download, raising=False)

    task_model = Task(name="cloud_download_task", task_type="cloud_download_task")
    task_instance = CloudDownloadTask(task_model)

    progress_updates = []
    async for progress in task_instance.run("demo"):
        progress_updates.append(progress)

    result = task_instance._task_model.result

    assert responses == ["demo-remote.zip"]
    assert download_calls[0][0] == "demo"
    assert Path(download_calls[0][1]) == archive_path
    assert result.project == "demo-remote.zip"
    assert result.bytes_downloaded == expected_size
    assert project.downloaded is True
    assert task_instance._task_model.progress.current == expected_size
    assert task_instance._task_model.progress.message == "Download completed"
    assert not archive_path.exists()


@pytest.mark.asyncio
async def test_cloud_download_task_missing_project(monkeypatch, project_manager):
    project_manager.add_project("demo")

    monkeypatch.setattr(
        "openscan_firmware.controllers.services.tasks.core.cloud_task._require_cloud_settings",
        lambda: SimpleNamespace(),
    )
    monkeypatch.setattr(
        "openscan_firmware.controllers.services.tasks.core.cloud_task.get_project_manager",
        lambda: project_manager,
    )

    task_model = Task(name="cloud_download_task", task_type="cloud_download_task")
    task_instance = CloudDownloadTask(task_model)

    async def _consume():
        async for _ in task_instance.run("unknown"):
            pass

    with pytest.raises(CloudServiceError):
        await _consume()


@pytest.mark.asyncio
async def test_cloud_download_task_no_download_link(monkeypatch, project_manager):
    project = _prepare_environment(monkeypatch, project_manager)

    attempts = 0

    def fake_get_project_info(name: str, token=None):
        nonlocal attempts
        attempts += 1
        return {}

    monkeypatch.setattr(
        "openscan_firmware.controllers.services.tasks.core.cloud_task.get_project_info",
        fake_get_project_info,
    )

    task_model = Task(name="cloud_download_task", task_type="cloud_download_task")
    task_instance = CloudDownloadTask(task_model)

    async def _consume():
        async for _ in task_instance.run(project.name):
            pass

    with pytest.raises(CloudServiceError, match="not ready"):
        await _consume()

    assert attempts == 3


@pytest.mark.asyncio
async def test_cloud_download_task_cancel(monkeypatch, project_manager, tmp_path):
    project = _prepare_environment(monkeypatch, project_manager)

    def fake_get_project_info(name: str, token=None):
        return {"dlink": "https://download/link", "status": "finished"}

    archive_path = tmp_path / "archive.zip"
    archive_path.write_bytes(b"zip-bytes")

    async def slow_stream(self, dlink: str, download_info: dict, stream_result: dict):  # noqa: ANN001
        await asyncio.sleep(0.05)
        if self.is_cancelled():
            raise CloudServiceError("Download cancelled")
        assert download_info == {"dlink": "https://download/link", "status": "finished"}
        downloaded = archive_path.stat().st_size
        stream_result["path"] = archive_path
        stream_result["bytes_downloaded"] = downloaded
        stream_result["total_bytes"] = downloaded
        yield downloaded // 2, downloaded
        await asyncio.sleep(0)
        yield downloaded, downloaded

    monkeypatch.setattr(
        "openscan_firmware.controllers.services.tasks.core.cloud_task.get_project_info",
        fake_get_project_info,
    )
    monkeypatch.setattr(
        "openscan_firmware.controllers.services.tasks.core.cloud_task.CloudDownloadTask._download_archive_stream",
        slow_stream,
    )

    task_model = Task(name="cloud_download_task", task_type="cloud_download_task")
    task_instance = CloudDownloadTask(task_model)

    async def _consume():
        async for _ in task_instance.run(project.name):
            pass

    run_task = asyncio.create_task(_consume())
    await asyncio.sleep(0.01)
    task_instance.cancel()

    with pytest.raises(CloudServiceError, match="Download cancelled"):
        await run_task

    assert project.downloaded is False
    assert task_instance._task_model.result is None
