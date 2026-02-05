import asyncio
from uuid import uuid4
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
import json
import os

from openscan_firmware.controllers.services.projects import ProjectManager, get_project_manager
from openscan_firmware.main import app
from openscan_firmware.models.task import Task, TaskStatus
from openscan_firmware.config.scan import ScanSetting
from openscan_firmware.config.camera import CameraSettings


@pytest.fixture(scope="function")
def api_project_manager(project_manager: ProjectManager) -> ProjectManager:
    """Provide a fresh ProjectManager for API tests."""
    return project_manager


@pytest.fixture(scope="function")
def api_client(api_project_manager: ProjectManager) -> TestClient:
    """Test client overriding ProjectManager dependency."""
    app.dependency_overrides[get_project_manager] = lambda: api_project_manager
    client = TestClient(app)
    try:
        yield client
    finally:
        app.dependency_overrides.clear()
        client.close()


def _prepare_scan(
    project_manager: ProjectManager,
    scan_settings: ScanSetting,
) -> tuple[ProjectManager, object, MagicMock]:
    project_name = f"proj-status-tests-{uuid4().hex[:8]}"
    project = project_manager.add_project(project_name)

    camera_controller = MagicMock()
    camera_controller.camera = MagicMock()
    camera_controller.camera.name = "mock-cam"
    camera_controller.settings = MagicMock()
    camera_controller.settings.model = CameraSettings()

    with patch("openscan_firmware.controllers.services.projects.asyncio.get_running_loop", return_value=MagicMock()):
        scan = project_manager.add_scan(
            project_name=project.name,
            camera_controller=camera_controller,
            scan_settings=scan_settings.model_copy(),
        )

    return project, scan, camera_controller


def test_pause_endpoint_persists_status(
    api_client: TestClient,
    api_project_manager: ProjectManager,
    sample_scan_settings: ScanSetting,
    latest_router_path,
) -> None:
    project, scan, camera_controller = _prepare_scan(api_project_manager, sample_scan_settings)
    scan.task_id = "task-123"
    asyncio.run(api_project_manager.save_scan_state(scan))

    paused_task = Task(name="scan_task", task_type="core", status=TaskStatus.PAUSED)
    task_manager_mock = MagicMock()
    task_manager_mock.pause_task = AsyncMock(return_value=paused_task)

    module_path = latest_router_path("projects")

    with patch("openscan_firmware.controllers.services.scans.get_task_manager", return_value=task_manager_mock), \
         patch("openscan_firmware.controllers.services.scans.get_project_manager", return_value=api_project_manager), \
         patch(f"{module_path}.get_project_manager", return_value=api_project_manager):
        response = api_client.patch(f"/latest/projects/{project.name}/scans/{scan.index}/pause")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == TaskStatus.PAUSED.value

    stored_scan = api_project_manager.get_scan_by_index(project.name, scan.index)
    assert stored_scan.status == TaskStatus.PAUSED
    task_manager_mock.pause_task.assert_awaited_once_with(scan.task_id)


def test_pause_endpoint_without_task(
    api_client: TestClient,
    api_project_manager: ProjectManager,
    sample_scan_settings: ScanSetting,
    latest_router_path,
) -> None:
    project, scan, _ = _prepare_scan(api_project_manager, sample_scan_settings)
    scan.task_id = None
    asyncio.run(api_project_manager.save_scan_state(scan))

    with patch(f"{latest_router_path('projects')}.get_project_manager", return_value=api_project_manager):
        response = api_client.patch(f"/latest/projects/{project.name}/scans/{scan.index}/pause")

    assert response.status_code == 409


def test_delete_scan_endpoint_removes_scan(
    api_client: TestClient,
    api_project_manager: ProjectManager,
    sample_scan_settings: ScanSetting,
    latest_router_path,
) -> None:
    project, scan, _ = _prepare_scan(api_project_manager, sample_scan_settings)

    with patch(f"{latest_router_path('projects')}.get_project_manager", return_value=api_project_manager):
        response = api_client.delete(f"/latest/projects/{project.name}/scans/{scan.index}")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["deleted"] == [f"{project.name}:scan{scan.index:02d}"]

    removed_scan = api_project_manager.get_scan_by_index(project.name, scan.index)
    assert removed_scan is None


def test_delete_scan_endpoint_rejects_legacy_path(
    api_client: TestClient,
    api_project_manager: ProjectManager,
    sample_scan_settings: ScanSetting,
    latest_router_path,
) -> None:
    project, scan, _ = _prepare_scan(api_project_manager, sample_scan_settings)

    with patch(f"{latest_router_path('projects')}.get_project_manager", return_value=api_project_manager):
        response = api_client.delete(f"/latest/projects/{project.name}/{scan.index}")

    # The legacy path should not exist anymore, so FastAPI returns 404.
    assert response.status_code == 404


def test_get_scan_path_endpoint_returns_path_json(
    api_client: TestClient,
    api_project_manager: ProjectManager,
    sample_scan_settings: ScanSetting,
    latest_router_path,
) -> None:
    project, scan, _ = _prepare_scan(api_project_manager, sample_scan_settings)

    scan_dir = os.path.join(project.path, f"scan{scan.index:02d}")
    os.makedirs(scan_dir, exist_ok=True)
    path_payload = {
        "project_name": project.name,
        "scan_index": scan.index,
        "points": [
            {
                "execution_step": 0,
                "original_step": 0,
                "polar": {"theta": 0.0, "fi": 0.0, "r": 1.0},
                "cartesian": {"x": 0.0, "y": 0.0, "z": 1.0},
            }
        ],
    }
    with open(os.path.join(scan_dir, "path.json"), "w", encoding="utf-8") as handle:
        json.dump(path_payload, handle)

    with patch("openscan_firmware.routers.next.projects.get_project_manager", return_value=api_project_manager):
        response = api_client.get(f"/next/projects/{project.name}/scans/{scan.index}/path")

    assert response.status_code == 200
    body = response.json()
    assert body["project_name"] == project.name
    assert body["scan_index"] == scan.index
    assert body["points"][0]["execution_step"] == 0


def test_resume_endpoint_persists_status(
    api_client: TestClient,
    api_project_manager: ProjectManager,
    sample_scan_settings: ScanSetting,
    latest_router_path,
) -> None:
    project, scan, camera_controller = _prepare_scan(api_project_manager, sample_scan_settings)
    scan.task_id = "task-456"
    asyncio.run(api_project_manager.save_scan_state(scan))

    resumed_task = Task(name="scan_task", task_type="core", status=TaskStatus.RUNNING)
    task_manager_mock = MagicMock()
    task_manager_mock.resume_task = AsyncMock(return_value=resumed_task)

    router_task_manager = MagicMock()
    router_task_manager.get_task_info.return_value = Task(
        name="scan_task",
        task_type="core",
        status=TaskStatus.PAUSED,
    )

    module_path = latest_router_path("projects")

    with patch("openscan_firmware.controllers.services.scans.get_task_manager", return_value=task_manager_mock), \
         patch("openscan_firmware.controllers.services.scans.get_project_manager", return_value=api_project_manager), \
         patch(f"{module_path}.get_project_manager", return_value=api_project_manager), \
         patch(f"{module_path}.get_camera_controller", return_value=camera_controller), \
         patch(f"{module_path}.get_task_manager", return_value=router_task_manager):
        response = api_client.patch(
            f"/latest/projects/{project.name}/scans/{scan.index}/resume",
            params={"camera_name": "mock-cam"},
        )

    assert response.status_code == 200
    assert response.json()["status"] == TaskStatus.RUNNING.value

    stored_scan = api_project_manager.get_scan_by_index(project.name, scan.index)
    assert stored_scan.status == TaskStatus.RUNNING
    task_manager_mock.resume_task.assert_awaited_once_with(scan.task_id)


def test_cancel_endpoint_persists_status(
    api_client: TestClient,
    api_project_manager: ProjectManager,
    sample_scan_settings: ScanSetting,
    latest_router_path,
) -> None:
    project, scan, _ = _prepare_scan(api_project_manager, sample_scan_settings)
    scan.task_id = "task-789"
    asyncio.run(api_project_manager.save_scan_state(scan))

    cancelled_task = Task(name="scan_task", task_type="core", status=TaskStatus.CANCELLED)
    task_manager_mock = MagicMock()
    task_manager_mock.cancel_task = AsyncMock(return_value=cancelled_task)

    with patch("openscan_firmware.controllers.services.scans.get_task_manager", return_value=task_manager_mock), \
         patch("openscan_firmware.controllers.services.scans.get_project_manager", return_value=api_project_manager), \
         patch(f"{latest_router_path('projects')}.get_project_manager", return_value=api_project_manager):
        response = api_client.patch(f"/latest/projects/{project.name}/scans/{scan.index}/cancel")

    assert response.status_code == 200
    assert response.json()["status"] == TaskStatus.CANCELLED.value

    stored_scan = api_project_manager.get_scan_by_index(project.name, scan.index)
    assert stored_scan.status == TaskStatus.CANCELLED
    task_manager_mock.cancel_task.assert_awaited_once_with(scan.task_id)


def test_cancel_paused_scan_endpoint_persists_status(
    api_client: TestClient,
    api_project_manager: ProjectManager,
    sample_scan_settings: ScanSetting,
    latest_router_path,
) -> None:
    project, scan, _ = _prepare_scan(api_project_manager, sample_scan_settings)
    scan.task_id = "task-paused-cancel"
    # Set initial state to PAUSED in persistence
    scan.status = TaskStatus.PAUSED
    asyncio.run(api_project_manager.save_scan_state(scan))

    cancelled_task = Task(name="scan_task", task_type="core", status=TaskStatus.CANCELLED)
    task_manager_mock = MagicMock()
    task_manager_mock.cancel_task = AsyncMock(return_value=cancelled_task)

    with patch("openscan_firmware.controllers.services.scans.get_task_manager", return_value=task_manager_mock), \
         patch("openscan_firmware.controllers.services.scans.get_project_manager", return_value=api_project_manager), \
         patch(f"{latest_router_path('projects')}.get_project_manager", return_value=api_project_manager):
        response = api_client.patch(f"/latest/projects/{project.name}/scans/{scan.index}/cancel")

    assert response.status_code == 200
    assert response.json()["status"] == TaskStatus.CANCELLED.value

    stored_scan = api_project_manager.get_scan_by_index(project.name, scan.index)
    assert stored_scan.status == TaskStatus.CANCELLED
    task_manager_mock.cancel_task.assert_awaited_once_with(scan.task_id)


def test_cancel_endpoint_missing_task_returns_conflict(
    api_client: TestClient,
    api_project_manager: ProjectManager,
    sample_scan_settings: ScanSetting,
    latest_router_path,
) -> None:
    project, scan, _ = _prepare_scan(api_project_manager, sample_scan_settings)
    scan.task_id = None
    asyncio.run(api_project_manager.save_scan_state(scan))

    with patch(f"{latest_router_path('projects')}.get_project_manager", return_value=api_project_manager), \
         patch("openscan_firmware.controllers.services.scans.cancel_scan", new_callable=AsyncMock) as cancel_mock:
        cancel_mock.return_value = None
        response = api_client.patch(f"/latest/projects/{project.name}/scans/{scan.index}/cancel")

    assert response.status_code == 409
