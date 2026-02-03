from datetime import datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openscan_firmware.config.cloud import CloudSettings, set_cloud_settings
from openscan_firmware.controllers.services.cloud_settings import set_active_source
from openscan_firmware.models.project import Project
from openscan_firmware.models.task import Task


@pytest.fixture(autouse=True)
def reset_cloud_state():
    set_cloud_settings(None)
    set_active_source(None)
    yield
    set_cloud_settings(None)
    set_active_source(None)


@pytest.fixture
def client(latest_router_loader):
    app = FastAPI()
    router_module = latest_router_loader("cloud")
    app.include_router(router_module.router, prefix="/next")
    with TestClient(app) as test_client:
        yield test_client


def test_cloud_status_success(client, monkeypatch, latest_router_path):
    module_path = latest_router_path("cloud")
    monkeypatch.setattr(
        f"{module_path}.cloud_service.get_status",
        lambda: {"ok": True},
    )
    monkeypatch.setattr(
        f"{module_path}.cloud_service.get_token_info",
        lambda: {"credit": 42},
    )
    monkeypatch.setattr(
        f"{module_path}.cloud_service.get_queue_estimate",
        lambda: {"minutes": 5},
    )
    monkeypatch.setattr(
        f"{module_path}.get_masked_active_settings",
        lambda: {"token": "***abcd"},
    )
    monkeypatch.setattr(
        f"{module_path}.get_active_source",
        lambda: "persistent",
    )
    monkeypatch.setattr(
        f"{module_path}.settings_file_exists",
        lambda: True,
    )

    response = client.get("/cloud/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == {"ok": True}
    assert payload["token_info"] == {"credit": 42}
    assert payload["queue_estimate"] == {"minutes": 5}
    assert payload["settings"] == {
        "settings": {"token": "***abcd"},
        "source": "persistent",
        "persisted": True,
    }
    assert payload["message"] is None


def test_update_cloud_settings_persists(client, monkeypatch, tmp_path, latest_router_path):
    module_path = latest_router_path("cloud")
    saved = {}

    def fake_save(settings: CloudSettings) -> Path:
        saved["settings"] = settings.model_dump()
        target = tmp_path / "cloud.json"
        target.write_text("{}", encoding="utf-8")
        return target

    monkeypatch.setattr(f"{module_path}.save_persistent_cloud_settings", fake_save)
    monkeypatch.setattr(f"{module_path}.get_masked_active_settings", lambda: {"token": "***5678"})
    monkeypatch.setattr(f"{module_path}.get_active_source", lambda: "persistent")
    monkeypatch.setattr(f"{module_path}.settings_file_exists", lambda: True)
    monkeypatch.setattr(f"{module_path}.set_active_source", lambda source: None)

    payload = {
        "user": "api-user",
        "password": "secret",
        "token": "token-value",
        "host": "http://example.com",
        "split_size": 1024,
    }

    response = client.post("/cloud/settings", json=payload)

    assert response.status_code == 200
    assert saved["settings"]["user"] == "api-user"
    assert response.json()["settings"]["token"] == "***5678"


def test_list_cloud_projects(client, monkeypatch, latest_router_path):
    module_path = latest_router_path("cloud")
    project = Project(
        name="demo",
        path="/tmp/demo",
        created=datetime.now(),
        scans={},
        uploaded=True,
        cloud_project_name=None,
    )

    class StubProjectManager:
        def __init__(self):
            self.projects = {project.name: project}
            self.calls: list[tuple[str, bool, str | None]] = []

        def get_all_projects(self):
            return self.projects

        def get_project_by_name(self, name: str):
            return self.projects.get(name)

        def mark_uploaded(self, name: str, uploaded: bool = True, cloud_project_name: str | None = None):
            self.calls.append((name, uploaded, cloud_project_name))
            proj = self.projects[name]
            proj.uploaded = uploaded
            proj.cloud_project_name = cloud_project_name
            return proj

    class StubTaskManager:
        def get_all_tasks_info(self):
            return [
                Task(
                    name="cloud_upload_task",
                    task_type="cloud_upload_task",
                    run_args=("demo",),
                    result={"project": "demo-remote.zip"},
                ),
                Task(name="other_task", task_type="other_task"),
            ]

    stub_pm = StubProjectManager()

    monkeypatch.setattr(f"{module_path}.get_project_manager", lambda: stub_pm)
    monkeypatch.setattr(f"{module_path}.get_task_manager", lambda: StubTaskManager())
    monkeypatch.setattr(
        f"{module_path}.cloud_service.get_project_info",
        lambda remote_name: {"project": remote_name, "state": "ready"},
    )

    response = client.get("/cloud/projects")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    entry = payload[0]
    assert entry["project"]["name"] == "demo"
    assert entry["project"]["cloud_project_name"] == "demo-remote.zip"
    assert entry["remote_project_name"] == "demo-remote.zip"
    assert entry["remote_info"] == {"project": "demo-remote.zip", "state": "ready"}
    assert len(entry["tasks"]) == 1
    assert entry["tasks"][0]["name"] == "cloud_upload_task"
    assert entry["message"] is None
    assert stub_pm.calls == [("demo", True, "demo-remote.zip")]


def test_reset_cloud_project(client, monkeypatch, latest_router_path):
    module_path = latest_router_path("cloud")
    project = Project(
        name="demo",
        path="/tmp/demo",
        created=datetime.now(),
        scans={},
        uploaded=True,
        cloud_project_name="demo-remote.zip",
    )

    class StubProjectManager:
        def __init__(self):
            self.projects = {project.name: project}
            self.calls = []

        def get_all_projects(self):  # pragma: no cover - not used
            return self.projects

        def get_project_by_name(self, name: str):
            return self.projects.get(name)

        def mark_uploaded(self, name: str, uploaded: bool, cloud_project_name: str | None = None):
            self.calls.append((name, uploaded, cloud_project_name))
            proj = self.projects[name]
            proj.uploaded = uploaded
            proj.cloud_project_name = cloud_project_name
            return proj

    stub_pm = StubProjectManager()

    monkeypatch.setattr(f"{module_path}.get_project_manager", lambda: stub_pm)
    monkeypatch.setattr(
        f"{module_path}.cloud_service.reset_project",
        lambda remote_name: {"reset": remote_name},
    )

    response = client.delete("/cloud/projects/demo")

    assert response.status_code == 200
    assert response.json()["remote_project"] == "demo-remote.zip"
    assert stub_pm.calls == [("demo", False, None)]
    assert project.cloud_project_name is None
