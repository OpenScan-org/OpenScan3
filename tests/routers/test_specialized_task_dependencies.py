"""HTTP contract tests for dependencies on specialized task endpoints."""

from importlib import import_module
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openscan_firmware.config.scan import ScanSetting
from openscan_firmware.models.task import Task, TaskStatus


PROJECT_ROUTER_MODULES = [
    "openscan_firmware.routers.v0_8.projects",
    "openscan_firmware.routers.v0_9.projects",
    "openscan_firmware.routers.next.projects",
]

FOCUS_ROUTER_MODULES = [
    "openscan_firmware.routers.v0_8.focus_stacking",
    "openscan_firmware.routers.v0_9.focus_stacking",
    "openscan_firmware.routers.next.focus_stacking",
]


def _client(router_module_name: str) -> TestClient:
    app = FastAPI()
    app.include_router(import_module(router_module_name).router, prefix="/api")
    return TestClient(app)


@pytest.mark.parametrize("router_module_name", PROJECT_ROUTER_MODULES)
def test_specialized_scan_endpoint_forwards_dependency(
    monkeypatch: pytest.MonkeyPatch,
    router_module_name: str,
) -> None:
    router_module = import_module(router_module_name)
    project_manager = MagicMock()
    scan = MagicMock()
    project_manager.add_scan.return_value = scan
    camera_controller = MagicMock()
    started_task = Task(
        name="scan_task",
        task_type="scan_task",
        status=TaskStatus.PENDING,
    )
    start_scan = AsyncMock(return_value=started_task)

    monkeypatch.setattr(router_module, "get_project_manager", lambda: project_manager)
    monkeypatch.setattr(router_module, "get_camera_controller", lambda _name: camera_controller)
    monkeypatch.setattr(router_module.scans, "start_scan", start_scan)

    with _client(router_module_name) as client:
        if router_module_name.endswith("next.projects"):
            response = client.post(
                "/api/projects/demo/scan",
                json={
                    "camera_name": "cam0",
                    "scan_settings": ScanSetting().model_dump(mode="json"),
                    "depends_on": "scan-prerequisite",
                },
            )
        else:
            response = client.post(
                "/api/projects/demo/scan"
                "?camera_name=cam0&depends_on=scan-prerequisite",
                json=ScanSetting().model_dump(mode="json"),
            )

    assert response.status_code == 200
    start_scan.assert_awaited_once_with(
        project_manager,
        scan,
        camera_controller,
        depends_on="scan-prerequisite",
    )


@pytest.mark.parametrize("router_module_name", FOCUS_ROUTER_MODULES)
def test_specialized_focus_stacking_endpoint_forwards_dependency(
    monkeypatch: pytest.MonkeyPatch,
    router_module_name: str,
) -> None:
    router_module = import_module(router_module_name)
    started_task = Task(
        name="focus_stacking_task",
        task_type="focus_stacking_task",
        status=TaskStatus.PENDING,
    )
    start_focus_stacking = AsyncMock(return_value=started_task)
    monkeypatch.setattr(
        router_module.focus_service,
        "start_focus_stacking",
        start_focus_stacking,
    )

    with _client(router_module_name) as client:
        response = client.post(
            "/api/projects/demo/scans/1/focus-stacking/start"
            "?depends_on=scan-prerequisite",
        )

    assert response.status_code == 200
    start_focus_stacking.assert_awaited_once_with(
        "demo",
        1,
        depends_on="scan-prerequisite",
    )

