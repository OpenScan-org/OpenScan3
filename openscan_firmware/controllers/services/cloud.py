"""Cloud service helpers for OpenScan."""

import io
import logging
import math
import pathlib
from dataclasses import dataclass
from tempfile import TemporaryFile
from typing import Any, BinaryIO, Iterator, Sequence
from zipfile import ZIP_DEFLATED, ZipFile

import requests

from openscan_firmware.config.cloud import CloudSettings, CloudConfigurationError, get_cloud_settings, mask_secret
from openscan_firmware.controllers.services.projects import ProjectManager, get_project_manager
from openscan_firmware.controllers.services.tasks.task_manager import get_task_manager
from openscan_firmware.models.task import TaskStatus
from openscan_firmware.models.project import Project


logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 60
ALLOWED_PHOTO_SUFFIXES = {".jpg", ".jpeg"}
UNSUPPORTED_PHOTO_SUFFIXES = {".png", ".dng", ".npy"}


class CloudServiceError(RuntimeError):
    """Raised when the cloud service encounters an unrecoverable error."""


@dataclass(frozen=True)
class CloudUploadResult:
    """Summary returned after uploading a project archive to the cloud."""

    project: str
    parts_uploaded: int
    archive_size_bytes: int
    photo_count: int
    create_response: dict[str, Any]
    start_response: dict[str, Any]


@dataclass(frozen=True)
class CloudDownloadResult:
    """Summary returned after downloading a reconstructed archive from the cloud."""

    project: str
    archive_size_bytes: int
    bytes_downloaded: int
    download_info: dict[str, Any]


def _require_cloud_settings() -> CloudSettings:
    """Retrieve configured cloud settings or raise a service error."""

    try:
        return get_cloud_settings()
    except CloudConfigurationError as exc:
        raise CloudServiceError("Cloud service is not configured.") from exc


def _cloud_request(
    method: str,
    path: str,
    params: dict[str, Any] | None = None,
    *,
    include_token: bool = True,
    token_override: str | None = None,
) -> requests.Response:
    """Perform an authenticated request against the cloud API."""

    settings = _require_cloud_settings()
    request_params: dict[str, Any] = {}
    if include_token:
        request_params["token"] = token_override or settings.token
    if params:
        request_params.update(params)

    base_url = str(settings.host).rstrip("/")
    url = f"{base_url}/{path}"
    log_params = dict(request_params)
    if "token" in log_params:
        log_params["token"] = mask_secret(str(log_params["token"]))

    logger.debug("Cloud request %s %s with params %s", method.upper(), url, log_params)

    response = requests.request(
        method,
        url,
        auth=(settings.user, settings.password),
        params=request_params,
        timeout=REQUEST_TIMEOUT,
    )
    return response


def _request_json(
    method: str,
    path: str,
    params: dict[str, Any] | None = None,
    *,
    include_token: bool = True,
    token_override: str | None = None,
) -> dict[str, Any]:
    """Execute a request and return the parsed JSON payload."""

    response = _cloud_request(
        method,
        path,
        params=params,
        include_token=include_token,
        token_override=token_override,
    )
    response.raise_for_status()
    return response.json()


def request_token(mail: str, forename: str, lastname: str) -> dict[str, Any]:
    """Request a new API token for a user.

    Args:
        mail: Contact email address.
        forename: Given name of the requester.
        lastname: Family name of the requester.

    Returns:
        dict[str, Any]: JSON payload returned by the cloud service.
    """

    return _request_json(
        "get",
        "requestToken",
        params={"mail": mail, "forename": forename, "lastname": lastname},
        include_token=False,
    )


def get_token_info(token: str | None = None) -> dict[str, Any]:
    """Retrieve quota information for a token.

    Args:
        token: Optional explicit token; defaults to configured token.

    Returns:
        dict[str, Any]: Quota details as reported by the cloud API.
    """

    return _request_json(
        "get",
        "getTokenInfo",
        params={"token": token or _require_cloud_settings().token},
        include_token=False,
    )


def get_project_info(project_name: str, token: str | None = None) -> dict[str, Any]:
    """Fetch metadata for a project that exists on the cloud.

    Args:
        project_name: Name of the remote project.
        token: Optional token override.

    Returns:
        dict[str, Any]: Remote project metadata.
    """

    return _request_json(
        "get",
        "getProjectInfo",
        params={"project": project_name},
        token_override=token,
    )


def reset_project(project_name: str, token: str | None = None) -> dict[str, Any]:
    """Reset an existing project on the cloud backend.

    Args:
        project_name: Identifier of the project to reset.
        token: Optional token override.

    Returns:
        dict[str, Any]: Response body describing the reset result.
    """

    return _request_json(
        "get",
        "resetProject",
        params={"project": project_name},
        token_override=token,
    )


def get_queue_estimate(token: str | None = None) -> dict[str, Any]:
    """Retrieve the current processing queue estimate.

    Args:
        token: Optional token override.

    Returns:
        dict[str, Any]: Estimated processing time information.
    """

    return _request_json(
        "get",
        "getQueueEstimate",
        include_token=True,
        token_override=token,
    )


def get_status() -> dict[str, Any]:
    """Return the public health status of the cloud service.

    Returns:
        dict[str, Any]: Status payload from the cloud API.
    """

    return _request_json("get", "status", include_token=False)


def _create_project(
    project_name: str,
    *,
    photos: int,
    filesize: int,
    parts: int,
    token: str | None = None,
) -> dict[str, Any]:
    """Create a new remote project and return the upload metadata."""

    return _request_json(
        "get",
        "createProject",
        params={
            "photos": photos,
            "filesize": filesize,
            "parts": parts,
            "project": project_name,
        },
        token_override=token,
    )


def _start_project(project_name: str, token: str | None = None) -> dict[str, Any]:
    """Start remote processing for a previously created project."""

    return _request_json(
        "get",
        "startProject",
        params={"project": project_name},
        token_override=token,
    )


# Internal helpers -----------------------------------------------------------


def _collect_project_photos(project: Project, *, log_warnings: bool = True) -> list[pathlib.Path]:
    """Return all photos that should be part of a cloud upload.

    Focus-stacked images take precedence: if a scan directory contains a
    ``stacked/`` subfolder with JPEGs, only those images are considered for that
    scan. Raw images remain the fallback for scans without stacked results.

    Args:
        project: Project for which photos should be collected.

    Returns:
        Sorted list of photo paths limited to allowed suffixes.
    """

    base_path = project.path_obj
    preferred_scans: set[pathlib.Path] = set()
    collected: list[pathlib.Path] = []

    for scan_dir in sorted(base_path.glob("scan*")):
        if not scan_dir.is_dir():
            continue

        stacked_dir = scan_dir / "stacked"
        if not stacked_dir.is_dir():
            continue

        stacked_photos = sorted(
            path
            for path in stacked_dir.rglob("*")
            if path.is_file() and path.suffix.lower() in ALLOWED_PHOTO_SUFFIXES
        )
        if stacked_photos:
            collected.extend(stacked_photos)
            preferred_scans.add(scan_dir.resolve())

    for file_path in sorted(base_path.rglob("*")):
        if not file_path.is_file():
            continue

        suffix = file_path.suffix.lower()
        if suffix not in ALLOWED_PHOTO_SUFFIXES:
            if log_warnings and suffix in UNSUPPORTED_PHOTO_SUFFIXES:
                logger.warning(
                    "Skipping unsupported photo '%s' for cloud upload",
                    file_path.relative_to(base_path),
                )
            continue

        # Skip raw photos when stacked results are available for the same scan
        if any(scan_dir in file_path.parents for scan_dir in preferred_scans):
            continue

        collected.append(file_path)

    base_path_resolved = base_path.resolve()
    return sorted(collected, key=lambda path: path.relative_to(base_path_resolved).as_posix())


# Public API -----------------------------------------------------------------


async def upload_project(
    project_name: str,
    *,
    project_manager: ProjectManager | None = None,
    token: str | None = None,
):
    """Schedule an upload task for an existing project.

    Args:
        project_name: Name of the project directory to upload.
        project_manager: Optional project manager to validate the project exists.
        token: Optional cloud token override forwarded to the task.

    Returns:
        Task: The TaskManager model describing the scheduled upload.

    Raises:
        CloudServiceError: If the project does not exist or cloud is not configured.
    """

    _require_cloud_settings()
    manager = project_manager or get_project_manager()
    project = manager.get_project_by_name(project_name)
    if project is None:
        raise CloudServiceError(f"Project '{project_name}' not found")

    if project.uploaded and project.cloud_project_name:
        raise CloudServiceError(
            "Project is already uploaded. Reset the cloud project before uploading again."
        )

    task_manager = get_task_manager()
    for task in task_manager.get_all_tasks_info():
        if (
            task.task_type == "cloud_upload_task"
            and task.run_args
            and task.run_args[0] == project_name
            and task.status in {TaskStatus.PENDING, TaskStatus.RUNNING}
        ):
            raise CloudServiceError(
                "An upload for this project is already in progress. Wait for completion or cancel it."
            )

    task = await task_manager.create_and_run_task(
        "cloud_upload_task",
        project_name,
        token=token,
    )
    return task


async def download_project(
    project_name: str,
    *,
    project_manager: ProjectManager | None = None,
    token: str | None = None,
    remote_project: str | None = None,
):
    """Schedule a download task for a reconstructed project archive.

    Args:
        project_name: Local project name whose reconstruction should be downloaded.
        project_manager: Optional project manager used to resolve the project.
        token: Optional cloud token override forwarded to the task.
        remote_project: Optional explicit remote project name, defaults to the stored cloud name.

    Returns:
        Task: The TaskManager model describing the scheduled download.

    Raises:
        CloudServiceError: If prerequisites are missing or a download is already running.
    """

    _require_cloud_settings()
    manager = project_manager or get_project_manager()
    project = manager.get_project_by_name(project_name)
    if project is None:
        raise CloudServiceError(f"Project '{project_name}' not found")

    remote_name = remote_project or project.cloud_project_name
    if not remote_name:
        raise CloudServiceError(
            "No remote project reference stored. Upload the project before downloading."
        )

    task_manager = get_task_manager()
    for task in task_manager.get_all_tasks_info():
        if (
            task.task_type == "cloud_download_task"
            and task.run_args
            and task.run_args[0] == project_name
            and task.status in {TaskStatus.PENDING, TaskStatus.RUNNING}
        ):
            raise CloudServiceError(
                "A download for this project is already in progress. Wait for completion or cancel it."
            )

    task = await task_manager.create_and_run_task(
        "cloud_download_task",
        project_name,
        token=token,
        remote_project=remote_name,
    )
    return task


def _upload_file(file_obj: BinaryIO, ulink: str) -> None:
    file_obj.seek(0)
    response = requests.post(
        ulink,
        data=file_obj,
        headers={"Content-Type": "application/octet-stream"},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()


def _build_project_archive(project: Project) -> tuple[TemporaryFile, int]:
    archive = TemporaryFile()
    base_path = project.path_obj

    seen_names: set[str] = set()
    with ZipFile(archive, "w", compression=ZIP_DEFLATED) as zipf:
        for file_path in _collect_project_photos(project):
            arcname = file_path.name
            if arcname in seen_names:
                arcname = str(file_path.relative_to(base_path)).replace("/", "_")
            seen_names.add(arcname)

            zipf.write(file_path, arcname)

    archive.seek(0, io.SEEK_END)
    size = archive.tell()
    archive.seek(0)
    return archive, size


def _count_project_photos(project: Project) -> int:
    return len(_collect_project_photos(project, log_warnings=False))


def _iter_chunks(file_obj: BinaryIO, chunk_size: int) -> Iterator[io.BytesIO]:
    file_obj.seek(0)
    while chunk := file_obj.read(chunk_size):
        yield io.BytesIO(chunk)