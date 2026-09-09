import asyncio
from importlib import import_module
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

import openscan_firmware.controllers.services.tasks.task_manager as task_manager_module
from openscan_firmware.controllers.services.tasks.task_manager import TaskManager
from openscan_firmware.main import app
from openscan_firmware.models.task import Task, TaskStatus


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


@pytest_asyncio.fixture
async def real_task_manager(monkeypatch: pytest.MonkeyPatch) -> TaskManager:
    task_manager = TaskManager()
    # get_task_manager() returns the module-level singleton, so point the API
    # at the isolated real manager used by this integration test.
    monkeypatch.setattr(task_manager_module, "task_manager", task_manager)
    from openscan_firmware.controllers.services.tasks.examples import demo_examples

    task_manager.register_task("hello_world_progress_task", demo_examples.HelloWorldProgressTask)
    task_manager.register_task("failing_task", demo_examples.FailingTask)
    yield task_manager


@pytest_asyncio.fixture
async def async_client(real_task_manager: TaskManager) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


async def _create_task(
    client: httpx.AsyncClient,
    api_version: str,
    task_name: str,
    *,
    kwargs: dict | None = None,
    depends_on: str | None = None,
) -> Task:
    payload = {"args": [], "kwargs": kwargs or {}}
    if depends_on is not None:
        payload["depends_on"] = depends_on

    response = await client.post(f"/{api_version}/tasks/{task_name}", json=payload)
    assert response.status_code == 202, response.text
    return Task.model_validate(response.json())


async def _wait_for_task(task_manager: TaskManager, task_id: str) -> Task:
    return await task_manager.wait_for_task(task_id, timeout=5)


@pytest.mark.parametrize(
    ("api_version", "router_module"),
    [
        ("v0.8", "openscan_firmware.routers.v0_8.tasks"),
        ("v0.9", "openscan_firmware.routers.v0_9.tasks"),
        ("next", "openscan_firmware.routers.next.tasks"),
    ],
)
def test_create_task_accepts_dependency(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    api_version: str,
    router_module: str,
) -> None:
    task_manager = MagicMock()
    task_manager.create_and_run_task = AsyncMock(
        return_value=Task(name="hello_world_progress_task", task_type="hello_world_progress_task")
    )
    monkeypatch.setattr(import_module(router_module), "get_task_manager", lambda: task_manager)

    response = client.post(
        f"/{api_version}/tasks/hello_world_progress_task",
        json={
            "args": ["demo"],
            "kwargs": {"some_option": True},
            "depends_on": "scan-task-id",
        },
    )

    assert response.status_code == 202
    task_manager.create_and_run_task.assert_awaited_once_with(
        "hello_world_progress_task",
        "demo",
        depends_on="scan-task-id",
        some_option=True,
    )


@pytest.mark.parametrize(
    ("api_version", "router_module"),
    [
        ("v0.8", "openscan_firmware.routers.v0_8.tasks"),
        ("v0.9", "openscan_firmware.routers.v0_9.tasks"),
        ("next", "openscan_firmware.routers.next.tasks"),
    ],
)
def test_create_task_dependency_is_optional(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    api_version: str,
    router_module: str,
) -> None:
    task_manager = MagicMock()
    task_manager.create_and_run_task = AsyncMock(
        return_value=Task(name="hello_world_progress_task", task_type="hello_world_progress_task")
    )
    monkeypatch.setattr(import_module(router_module), "get_task_manager", lambda: task_manager)

    response = client.post(
        f"/{api_version}/tasks/hello_world_progress_task",
        json={"args": [], "kwargs": {}},
    )

    assert response.status_code == 202
    task_manager.create_and_run_task.assert_awaited_once_with(
        "hello_world_progress_task",
        depends_on=None,
    )


@pytest.mark.parametrize(
    ("task_name", "expected_endpoint"),
    [
        ("scan_task", "POST /projects/{project_name}/scan"),
        (
            "focus_stacking_task",
            "POST /projects/{project_name}/scans/{scan_index}/focus-stacking/start",
        ),
        ("cloud_upload_task", "POST /projects/{project_name}/upload"),
    ],
)
def test_domain_tasks_must_use_project_specific_endpoints(
    client: TestClient,
    task_name: str,
    expected_endpoint: str,
) -> None:
    response = client.post(
        f"/next/tasks/{task_name}",
        json={"args": [], "kwargs": {}},
    )

    assert response.status_code == 400
    assert expected_endpoint in response.json()["detail"]
    assert "experimental or custom tasks" in response.json()["detail"]


@pytest.mark.asyncio
async def test_task_api_runs_real_three_step_chain(
    async_client: httpx.AsyncClient,
    real_task_manager: TaskManager,
) -> None:
    first = await _create_task(
        async_client,
        "next",
        "hello_world_progress_task",
        kwargs={"total_steps": 2, "interval": 0.15},
    )
    second = await _create_task(
        async_client,
        "next",
        "hello_world_progress_task",
        kwargs={"total_steps": 1, "interval": 0.01},
        depends_on=first.id,
    )
    third = await _create_task(
        async_client,
        "next",
        "hello_world_progress_task",
        kwargs={"total_steps": 1, "interval": 0.01},
        depends_on=second.id,
    )

    assert real_task_manager.get_task_info(first.id).status == TaskStatus.RUNNING
    assert real_task_manager.get_task_info(second.id).status == TaskStatus.PENDING
    assert real_task_manager.get_task_info(third.id).status == TaskStatus.PENDING

    first_result, second_result, third_result = await asyncio.gather(
        _wait_for_task(real_task_manager, first.id),
        _wait_for_task(real_task_manager, second.id),
        _wait_for_task(real_task_manager, third.id),
    )

    assert first_result.status == TaskStatus.COMPLETED
    assert second_result.status == TaskStatus.COMPLETED
    assert third_result.status == TaskStatus.COMPLETED
    assert real_task_manager.get_task_info(second.id).depends_on is None
    assert real_task_manager.get_task_info(third.id).depends_on is None


@pytest.mark.asyncio
async def test_task_api_propagates_failure_through_real_chain(
    async_client: httpx.AsyncClient,
    real_task_manager: TaskManager,
) -> None:
    first = await _create_task(
        async_client,
        "next",
        "failing_task",
        kwargs={"error_message": "first task failed"},
    )
    second = await _create_task(
        async_client,
        "next",
        "hello_world_progress_task",
        kwargs={"total_steps": 1},
        depends_on=first.id,
    )
    third = await _create_task(
        async_client,
        "next",
        "hello_world_progress_task",
        kwargs={"total_steps": 1},
        depends_on=second.id,
    )

    first_result, second_result, third_result = await asyncio.gather(
        _wait_for_task(real_task_manager, first.id),
        _wait_for_task(real_task_manager, second.id),
        _wait_for_task(real_task_manager, third.id),
    )

    assert first_result.status == TaskStatus.ERROR
    assert first_result.error == "first task failed"
    assert second_result.status == TaskStatus.ERROR
    assert third_result.status == TaskStatus.ERROR
    assert "Dependency task" in second_result.error
    assert "Dependency task" in third_result.error
    assert real_task_manager.get_task_info(second.id).started_at is None
    assert real_task_manager.get_task_info(third.id).started_at is None


@pytest.mark.asyncio
async def test_task_api_propagates_middle_task_failure_to_downstream_task(
    async_client: httpx.AsyncClient,
    real_task_manager: TaskManager,
) -> None:
    first = await _create_task(
        async_client,
        "next",
        "hello_world_progress_task",
        kwargs={"total_steps": 1, "interval": 0.05},
    )
    second = await _create_task(
        async_client,
        "next",
        "failing_task",
        kwargs={"error_message": "middle task failed"},
        depends_on=first.id,
    )
    third = await _create_task(
        async_client,
        "next",
        "hello_world_progress_task",
        kwargs={"total_steps": 1},
        depends_on=second.id,
    )

    first_result, second_result, third_result = await asyncio.gather(
        _wait_for_task(real_task_manager, first.id),
        _wait_for_task(real_task_manager, second.id),
        _wait_for_task(real_task_manager, third.id),
    )

    assert first_result.status == TaskStatus.COMPLETED
    assert second_result.status == TaskStatus.ERROR
    assert second_result.error == "middle task failed"
    assert third_result.status == TaskStatus.ERROR
    assert "Dependency task" in third_result.error
    assert real_task_manager.get_task_info(third.id).started_at is None
