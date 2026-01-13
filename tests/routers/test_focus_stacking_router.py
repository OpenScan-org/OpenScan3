import pytest
from fastapi.testclient import TestClient

from openscan_firmware.main import app
from openscan_firmware.models.task import Task, TaskStatus


@pytest.fixture(name="client")
def fixture_client() -> TestClient:
    """Provide a TestClient for the FastAPI app."""
    with TestClient(app) as test_client:
        yield test_client


def _make_task(status: TaskStatus = TaskStatus.RUNNING) -> Task:
    return Task(name="focus", task_type="core", status=status)


@pytest.mark.parametrize("endpoint", [
    ("start", "post"),
    ("pause", "patch"),
    ("resume", "patch"),
    ("cancel", "patch"),
])
def test_focus_stacking_endpoints_available_only_in_v0_5(monkeypatch, client: TestClient, endpoint: tuple[str, str]):
    action, method = endpoint

    async def _stub(*args, **kwargs):
        return _make_task()

    monkeypatch.setattr(
        "openscan_firmware.routers.focus_stacking.focus_service." f"{action}_focus_stacking",
        _stub,
    )

    url = f"/v0.5/projects/demo/scans/1/focus-stacking/{action}"
    response = getattr(client, method)(url)

    assert response.status_code == 200
    assert response.json()["status"] == TaskStatus.RUNNING

    legacy_url = f"/v0.4/projects/demo/scans/1/focus-stacking/{action}"
    legacy_response = getattr(client, method)(legacy_url)
    assert legacy_response.status_code == 404


@pytest.mark.parametrize("endpoint", [
    ("pause", "Focus stacking is not running"),
    ("cancel", "Focus stacking is not running"),
    ("resume", "Focus stacking is not paused"),
])
def test_focus_stacking_conflict(monkeypatch, client: TestClient, endpoint: tuple[str, str]):
    action, message = endpoint

    async def _stub(*args, **kwargs):
        return None

    monkeypatch.setattr(
        "openscan_firmware.routers.focus_stacking.focus_service." f"{action}_focus_stacking",
        _stub,
    )

    response = client.patch(f"/v0.5/projects/demo/scans/1/focus-stacking/{action}")
    assert response.status_code == 409
    assert response.json()["detail"] == message


@pytest.mark.parametrize("endpoint", [
    ("start", "post"),
    ("pause", "patch"),
    ("resume", "patch"),
    ("cancel", "patch"),
])
def test_focus_stacking_not_found(monkeypatch, client: TestClient, endpoint: tuple[str, str]):
    action, method = endpoint

    async def _stub(*args, **kwargs):
        raise ValueError("Scan not found")

    monkeypatch.setattr(
        "openscan_firmware.routers.focus_stacking.focus_service." f"{action}_focus_stacking",
        _stub,
    )

    response = getattr(client, method)(f"/v0.5/projects/demo/scans/1/focus-stacking/{action}")
    assert response.status_code == 404
    assert response.json()["detail"] == "Scan not found"
