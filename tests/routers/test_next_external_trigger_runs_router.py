from unittest.mock import AsyncMock, patch

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from openscan_firmware.controllers.services.external_trigger_runs import ExternalTriggerRunManager
from openscan_firmware.models.external_trigger_run import ExternalTriggerPoint, ExternalTriggerRunPath
from openscan_firmware.models.paths import CartesianPoint3D, PolarPoint3D
from openscan_firmware.models.task import Task, TaskStatus
from openscan_firmware.routers.next.external_trigger_runs import router


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


@pytest_asyncio.fixture
async def external_trigger_runs_client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


def _sample_settings() -> dict:
    return {
        "points": 3,
        "trigger_name": "external-camera",
        "pre_trigger_delay_ms": 10,
        "post_trigger_delay_ms": 20,
    }


@pytest.mark.asyncio
async def test_create_external_trigger_run_returns_task(external_trigger_runs_client: httpx.AsyncClient) -> None:
    created_task = Task(
        id="task-router-1",
        name="external_trigger_run_task",
        task_type="core",
        status=TaskStatus.RUNNING,
    )

    with patch(
        "openscan_firmware.routers.next.external_trigger_runs.start_external_trigger_run",
        AsyncMock(return_value=created_task),
    ):
        response = await external_trigger_runs_client.post(
            "/external-trigger/runs/",
            json={
                "label": "router-run",
                "description": "test run",
                "settings": _sample_settings(),
            },
        )

    assert response.status_code == 202
    body = response.json()
    assert body["id"] == "task-router-1"
    assert body["status"] == TaskStatus.RUNNING.value


@pytest.mark.asyncio
async def test_get_external_trigger_run_path_returns_json(
    tmp_path,
    external_trigger_runs_client: httpx.AsyncClient,
) -> None:
    manager = ExternalTriggerRunManager(path=tmp_path)
    manager.save_path_data(
        ExternalTriggerRunPath(
            task_id="task-router-path",
            total_steps=1,
            points=[
                ExternalTriggerPoint(
                    execution_step=0,
                    original_step=0,
                    polar_coordinates=PolarPoint3D(theta=10.0, fi=20.0),
                    cartesian_coordinates=CartesianPoint3D(x=1.0, y=2.0, z=3.0),
                )
            ],
        )
    )

    with patch(
        "openscan_firmware.routers.next.external_trigger_runs.get_external_trigger_run_manager",
        return_value=manager,
    ):
        response = await external_trigger_runs_client.get("/external-trigger/runs/task-router-path/path")

    assert response.status_code == 200
    body = response.json()
    assert body["task_id"] == "task-router-path"
    assert body["total_steps"] == 1
    assert len(body["points"]) == 1


@pytest.mark.asyncio
async def test_get_external_trigger_run_returns_task(external_trigger_runs_client: httpx.AsyncClient) -> None:
    task = Task(
        id="task-router-2",
        name="external_trigger_run_task",
        task_type="external_trigger_run_task",
        status=TaskStatus.PENDING,
    )
    with patch(
        "openscan_firmware.routers.next.external_trigger_runs.get_external_trigger_task",
        return_value=task,
    ):
        response = await external_trigger_runs_client.get("/external-trigger/runs/task-router-2")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "task-router-2"
    assert body["status"] == TaskStatus.PENDING.value


@pytest.mark.asyncio
async def test_list_external_trigger_runs_returns_tasks(external_trigger_runs_client: httpx.AsyncClient) -> None:
    task = Task(
        id="task-router-4",
        name="external_trigger_run_task",
        task_type="external_trigger_run_task",
        status=TaskStatus.RUNNING,
    )
    with patch(
        "openscan_firmware.routers.next.external_trigger_runs.list_external_trigger_tasks",
        return_value=[task],
    ):
        response = await external_trigger_runs_client.get("/external-trigger/runs/")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["id"] == "task-router-4"


@pytest.mark.asyncio
async def test_pause_external_trigger_run_returns_task(external_trigger_runs_client: httpx.AsyncClient) -> None:
    paused_task = Task(
        id="task-router-5",
        name="external_trigger_run_task",
        task_type="core",
        status=TaskStatus.PAUSED,
    )

    with patch(
        "openscan_firmware.routers.next.external_trigger_runs.pause_external_trigger_run",
        AsyncMock(return_value=paused_task),
    ):
        response = await external_trigger_runs_client.patch("/external-trigger/runs/task-router-5/pause")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "task-router-5"
    assert body["status"] == TaskStatus.PAUSED.value
