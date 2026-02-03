"""Cloud-specific API endpoints exposing status, configuration and project helpers."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from openscan_firmware.config.cloud import CloudSettings, mask_secret, set_cloud_settings
from openscan_firmware.controllers.services import cloud as cloud_service
from openscan_firmware.controllers.services.cloud import CloudServiceError
from openscan_firmware.controllers.services.cloud_settings import (
    get_active_source,
    get_masked_active_settings,
    save_persistent_cloud_settings,
    set_active_source,
    settings_file_exists,
)
from openscan_firmware.controllers.services.projects import ProjectManager, get_project_manager
from openscan_firmware.controllers.services.tasks.task_manager import get_task_manager
from openscan_firmware.models.project import Project
from openscan_firmware.models.task import Task

router = APIRouter(
    prefix="/cloud",
    tags=["cloud"],
    responses={404: {"description": "Not found"}},
)

logger = logging.getLogger(__name__)
_TOKEN_PARAM_PATTERN = re.compile(r"(token=)([^&\s]+)")


class CloudSettingsResponse(BaseModel):
    """Masked cloud settings including metadata."""

    settings: dict[str, Any] | None = None
    source: str | None = None
    persisted: bool = False


class CloudStatusResponse(BaseModel):
    """Aggregated view of the cloud backend status."""

    status: dict[str, Any] | None = None
    token_info: dict[str, Any] | None = None
    queue_estimate: dict[str, Any] | None = None
    settings: CloudSettingsResponse
    message: str | None = None


class CloudProjectStatus(BaseModel):
    """Local project enriched with cloud metadata and related tasks."""

    project: Project
    remote_project_name: str | None = None
    remote_info: dict[str, Any] | None = None
    tasks: list[Task] = Field(default_factory=list)
    message: str | None = None


async def _fetch_remote_info(remote_name: str) -> tuple[dict[str, Any] | None, str | None]:
    try:
        data = await asyncio.to_thread(cloud_service.get_project_info, remote_name)
        return data, None
    except CloudServiceError as exc:  # pragma: no cover - exercised in error test
        return None, str(exc)
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.exception("Failed to fetch remote project info for %s", remote_name)
        return None, str(exc)


def _collect_tasks_by_project() -> dict[str, list[Task]]:
    task_manager = get_task_manager()
    mapping: dict[str, list[Task]] = {}
    for task in task_manager.get_all_tasks_info():
        if task.task_type != "cloud_upload_task" or not task.run_args:
            continue
        project_name = task.run_args[0]
        mapping.setdefault(project_name, []).append(task)
    return mapping


def _extract_remote_name_from_tasks(tasks: list[Task]) -> str | None:
    for task in tasks:
        result = task.result
        if isinstance(result, dict) and "project" in result:
            return str(result["project"])
        if hasattr(result, "project"):
            return str(getattr(result, "project"))
    return None


async def _build_project_status(
    project: Project,
    tasks_by_project: dict[str, list[Task]],
    project_manager: ProjectManager,
) -> CloudProjectStatus:
    remote_info = None
    message = None
    remote_name = project.cloud_project_name
    tasks = tasks_by_project.get(project.name, [])

    if not remote_name:
        remote_name = _extract_remote_name_from_tasks(tasks)
        if remote_name:
            try:
                project_manager.mark_uploaded(project.name, True, remote_name)
                refreshed = project_manager.get_project_by_name(project.name)
                if refreshed is not None:
                    project = refreshed
            except ValueError:
                logger.warning(
                    "Failed to persist derived remote project name '%s' for '%s'",
                    remote_name,
                    project.name,
                )
        elif tasks:
            message = "Remote project name not available yet. Upload still running?"

    if remote_name:
        fetched_info, fetch_message = await _fetch_remote_info(remote_name)
        remote_info = fetched_info
        if fetch_message:
            message = f"{message} | {fetch_message}".strip(" |") if message else fetch_message

    return CloudProjectStatus(
        project=project.model_copy(),
        remote_project_name=remote_name,
        remote_info=remote_info,
        tasks=[task.model_copy() for task in tasks],
        message=message,
    )


def _build_settings_response() -> CloudSettingsResponse:
    return CloudSettingsResponse(
        settings=get_masked_active_settings(),
        source=get_active_source(),
        persisted=settings_file_exists(),
    )


def _mask_tokens(text: str | None) -> str | None:
    if not text:
        return text

    return _TOKEN_PARAM_PATTERN.sub(
        lambda match: f"{match.group(1)}{mask_secret(match.group(2))}",
        text,
    )


@router.get("/status", response_model=CloudStatusResponse)
async def get_cloud_status() -> CloudStatusResponse:
    """Return aggregated status information for the cloud backend.

    Returns:
        CloudStatusResponse: A response object containing the status of the cloud backend
    """

    status = token_info = queue_estimate = None
    messages: list[str] = []

    try:
        status = await asyncio.to_thread(cloud_service.get_status)
    except CloudServiceError as exc:
        messages.append(f"Status unavailable: {_mask_tokens(str(exc))}")
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.exception("Cloud status request failed")
        messages.append(f"Status request failed: {_mask_tokens(str(exc))}")

    try:
        token_info = await asyncio.to_thread(cloud_service.get_token_info)
    except CloudServiceError as exc:
        messages.append(f"Token info unavailable: {_mask_tokens(str(exc))}")
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.exception("Token info request failed")
        messages.append(f"Token info request failed: {_mask_tokens(str(exc))}")

    try:
        queue_estimate = await asyncio.to_thread(cloud_service.get_queue_estimate)
    except CloudServiceError as exc:
        messages.append(f"Queue estimate unavailable: {_mask_tokens(str(exc))}")
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.exception("Queue estimate request failed")
        messages.append(f"Queue estimate request failed: {_mask_tokens(str(exc))}")

    return CloudStatusResponse(
        status=status,
        token_info=token_info,
        queue_estimate=queue_estimate,
        settings=_build_settings_response(),
        message=_mask_tokens(" | ".join(messages)) if messages else None,
    )


@router.get("/settings", response_model=CloudSettingsResponse)
async def get_cloud_settings() -> CloudSettingsResponse:
    """Return the masked active cloud configuration.

    Returns:
        CloudSettingsResponse: A response object containing the masked active cloud configuration
    """

    return _build_settings_response()


@router.post("/settings", response_model=CloudSettingsResponse)
async def update_cloud_settings(new_settings: CloudSettings) -> CloudSettingsResponse:
    """Persist and activate new cloud settings.

    Args:
        new_settings: The new cloud settings to persist and activate

    Returns:
        CloudSettingsResponse: A response object containing the masked active cloud configuration
    """

    set_cloud_settings(new_settings)
    await asyncio.to_thread(save_persistent_cloud_settings, new_settings)
    set_active_source("persistent")
    return _build_settings_response()


@router.get("/projects", response_model=list[CloudProjectStatus])
async def list_cloud_projects() -> list[CloudProjectStatus]:
    """Return all local projects enriched with cloud metadata.

    Returns:
        list[CloudProjectStatus]: A list of cloud project status objects
    """

    project_manager = get_project_manager()
    tasks_by_project = _collect_tasks_by_project()

    statuses: list[CloudProjectStatus] = []
    for project in project_manager.get_all_projects().values():
        statuses.append(await _build_project_status(project, tasks_by_project, project_manager))
    return statuses


@router.get("/projects/{project_name}", response_model=CloudProjectStatus)
async def get_cloud_project(project_name: str) -> CloudProjectStatus:
    """Return cloud details for a single local project.

    Args:
        project_name: The name of the project to get the cloud details for

    Returns:
        CloudProjectStatus: A response object containing the cloud project status
    """

    project_manager = get_project_manager()
    project = project_manager.get_project_by_name(project_name)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project '{project_name}' not found")

    tasks_by_project = _collect_tasks_by_project()
    return await _build_project_status(project, tasks_by_project, project_manager)


@router.delete("/projects/{project_name}")
async def reset_cloud_project(project_name: str) -> dict[str, Any]:
    """Reset the remote project and clear the local linkage.

    Invokes the cloud backend's `resetProject` action, which removes the
    current reconstruction job (queue progress, generated models and downloads)
    and frees the remote project name for another upload.
    Locally the project is marked as not uploaded anymore and the stored `cloud_project_name` is
    cleared, while the on-disk files stay untouched.

    Args:
        project_name: The name of the project to reset the remote project for

    Returns:
        dict[str, Any]: A response object containing the result of the reset operation
    """

    project_manager = get_project_manager()
    project = project_manager.get_project_by_name(project_name)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project '{project_name}' not found")

    remote_name = project.cloud_project_name
    if not remote_name:
        raise HTTPException(status_code=404, detail=f"Project '{project_name}' has no recorded remote counterpart")

    try:
        response = await asyncio.to_thread(cloud_service.reset_project, remote_name)
    except CloudServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    project_manager.mark_uploaded(project_name, False)
    return {"project": project_name, "remote_project": remote_name, "response": response}


@router.post("/projects/{project_name}/download", response_model=Task)
async def download_project_from_cloud(
    project_name: str,
    token_override: str | None = None,
    remote_project: str | None = None,
) -> Task:
    """Schedule an asynchronous cloud download for a project's reconstruction.

    Args:
        project_name: Local project name whose reconstruction should be downloaded.
        token_override: Optional token override forwarded to the download task.
        remote_project: Optional explicit remote project identifier; defaults to stored linkage.

    Returns:
        Task: The TaskManager model describing the scheduled download.
    """

    try:
        task = await cloud_service.download_project(
            project_name,
            token=token_override,
            remote_project=remote_project,
        )
    except CloudServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return task
