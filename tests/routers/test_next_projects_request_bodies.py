"""Request-contract tests for the next projects router."""

from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from openscan_firmware.config.scan import ScanSetting
from openscan_firmware.main import make_version_app
from openscan_firmware.models.task import Task
from openscan_firmware.routers.next import projects as projects_router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(projects_router.router, prefix="/next")
    return TestClient(app)


def test_new_project_accepts_description_in_json_body(monkeypatch, project_manager) -> None:
    monkeypatch.setattr(projects_router, "get_project_manager", lambda: project_manager)

    with _client() as client:
        response = client.post(
            "/next/projects/json-project",
            json={"project_description": "Created from a JSON request body."},
        )

    assert response.status_code == 200
    assert response.json()["description"] == "Created from a JSON request body."


def test_add_scan_accepts_all_input_in_json_body(monkeypatch) -> None:
    project_manager = MagicMock()
    scan = MagicMock()
    project_manager.add_scan.return_value = scan
    camera_controller = MagicMock()
    started_task = Task(name="scan", task_type="core")

    monkeypatch.setattr(projects_router, "get_project_manager", lambda: project_manager)
    monkeypatch.setattr(projects_router, "get_camera_controller", lambda _name: camera_controller)
    monkeypatch.setattr(projects_router.scans, "start_scan", AsyncMock(return_value=started_task))

    with _client() as client:
        response = client.post(
            "/next/projects/json-project/scan",
            json={
                "camera_name": "cam0",
                "scan_settings": ScanSetting().model_dump(mode="json"),
                "scan_description": "Created from a JSON request body.",
                "depends_on": "previous-task",
            },
        )

    assert response.status_code == 200
    project_manager.add_scan.assert_called_once_with(
        "json-project",
        camera_controller,
        ScanSetting(),
        "Created from a JSON request body.",
    )
    projects_router.scans.start_scan.assert_awaited_once_with(
        project_manager,
        scan,
        camera_controller,
        depends_on="previous-task",
    )


def test_next_projects_openapi_uses_json_request_bodies() -> None:
    schema = make_version_app("next").openapi()
    project_post = schema["paths"]["/projects/{project_name}"]["post"]
    scan_post = schema["paths"]["/projects/{project_name}/scan"]["post"]

    assert [parameter["name"] for parameter in project_post["parameters"]] == ["project_name"]
    assert [parameter["name"] for parameter in scan_post["parameters"]] == ["project_name"]
    assert "$ref" in project_post["requestBody"]["content"]["application/json"]["schema"]
    assert "$ref" in scan_post["requestBody"]["content"]["application/json"]["schema"]
    assert scan_post["operationId"] == "add_scan"


def test_versioned_projects_openapi_contracts_remain_unchanged() -> None:
    for version in ("0.8", "0.9"):
        schema = make_version_app(version).openapi()
        project_post = schema["paths"]["/projects/{project_name}"]["post"]
        scan_post = schema["paths"]["/projects/{project_name}/scan"]["post"]

        assert [parameter["name"] for parameter in project_post["parameters"]] == [
            "project_name",
            "project_description",
        ]
        assert "requestBody" not in project_post
        assert [parameter["name"] for parameter in scan_post["parameters"]] == [
            "project_name",
            "camera_name",
            "scan_description",
            "depends_on",
        ]
        assert scan_post["requestBody"]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ScanSetting"
        }
        assert scan_post["operationId"] == "add_scan_with_description"
