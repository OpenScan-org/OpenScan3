"""Background tasks for interacting with the OpenScan Cloud."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

import requests
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from openscan_firmware.controllers.services.cloud import (
    CloudDownloadResult,
    CloudServiceError,
    CloudUploadResult,
    REQUEST_TIMEOUT,
    _build_project_archive,
    _count_project_photos,
    _create_project,
    _iter_chunks,
    _require_cloud_settings,
    _start_project,
    get_project_info,
    _upload_file,
)
from openscan_firmware.controllers.services.projects import get_project_manager
from openscan_firmware.controllers.services.tasks.base_task import BaseTask
from openscan_firmware.models.task import TaskProgress


logger = logging.getLogger(__name__)

_DOWNLOAD_RETRY_ATTEMPTS = 3
_DOWNLOAD_RETRY_DELAY_SECONDS = 3
_DOWNLOAD_CHUNK_SIZE = 512 * 1024


class CloudUploadTask(BaseTask):
    """Upload an existing project directory to the OpenScan Cloud."""

    task_name = "cloud_upload_task"
    task_category = "core"
    is_exclusive = False
    is_blocking = False

    async def run(self, project_name: str, token: str | None = None) -> CloudUploadResult:
        """Perform the upload asynchronously with background offloading."""

        settings = _require_cloud_settings()
        project_manager = get_project_manager()
        project = project_manager.get_project_by_name(project_name)
        if project is None:
            raise CloudServiceError(f"Project '{project_name}' not found")

        archive_file, archive_size = await asyncio.to_thread(_build_project_archive, project)
        photo_count = await asyncio.to_thread(_count_project_photos, project)
        parts_required = max(1, (archive_size + settings.split_size - 1) // settings.split_size)

        remote_project_name = _generate_remote_project_name(project.name)

        self._task_model.progress = TaskProgress(
            current=0,
            total=parts_required,
            message="Preparing upload",
        )

        logger.info(
            "[%s] Uploading project '%s' with %s photos (%s bytes) split into %s part(s)",
            self.id,
            project.name,
            photo_count,
            archive_size,
            parts_required,
        )

        try:
            create_response = await asyncio.to_thread(
                _create_project,
                remote_project_name,
                photos=photo_count,
                filesize=archive_size,
                parts=parts_required,
                token=token,
            )

            upload_links = create_response.get("ulink")
            if not isinstance(upload_links, list) or len(upload_links) != parts_required:
                raise CloudServiceError(
                    f"Unexpected upload link information for project '{project.name}'"
                )

            chunk_iterator = _iter_chunks(archive_file, settings.split_size)
            for index in range(1, parts_required + 1):
                await self.wait_for_pause()
                if self.is_cancelled():
                    logger.warning("[%s] Upload cancelled at part %s", self.id, index)
                    raise CloudServiceError("Upload cancelled")

                chunk = await asyncio.to_thread(next, chunk_iterator, None)
                if chunk is None:
                    raise CloudServiceError("Upload aborted: missing chunk data")

                part_link = upload_links[index - 1]
                logger.debug(
                    "[%s] Uploading part %s/%s for project '%s'",
                    self.id,
                    index,
                    parts_required,
                    project.name,
                )
                await asyncio.to_thread(_upload_file, chunk, part_link)

                self._task_model.progress = TaskProgress(
                    current=index,
                    total=parts_required,
                    message=f"Uploaded part {index}/{parts_required}",
                )

            start_response = await asyncio.to_thread(_start_project, remote_project_name, token)
            logger.info("[%s] Started cloud processing for project '%s'", self.id, project.name)

            result = CloudUploadResult(
                project=remote_project_name,
                parts_uploaded=parts_required,
                archive_size_bytes=archive_size,
                photo_count=photo_count,
                create_response=create_response,
                start_response=start_response,
            )
            self._task_model.result = result
            self._task_model.progress.message = "Upload completed"

            await asyncio.to_thread(
                project_manager.mark_uploaded,
                project_name,
                True,
                remote_project_name,
            )

            return result
        finally:
            await asyncio.to_thread(archive_file.close)


class CloudDownloadTask(BaseTask):
    """Download and install a reconstructed project archive from the OpenScan Cloud."""

    task_name = "cloud_download_task"
    task_category = "core"
    is_exclusive = False
    is_blocking = False

    async def run(
        self,
        project_name: str,
        token: str | None = None,
        remote_project: str | None = None,
    ) -> CloudDownloadResult:
        """Retrieve the reconstructed archive and unpack it into the project directory."""

        _require_cloud_settings()
        project_manager = get_project_manager()
        project = project_manager.get_project_by_name(project_name)
        if project is None:
            raise CloudServiceError(f"Project '{project_name}' not found")

        remote_name = remote_project or project.cloud_project_name
        if not remote_name:
            raise CloudServiceError(
                "No remote project name available. Upload the project before downloading."
            )

        download_info: dict[str, Any] | None = None
        dlink: str | None = None
        for attempt in range(1, _DOWNLOAD_RETRY_ATTEMPTS + 1):
            await self.wait_for_pause()
            if self.is_cancelled():
                raise CloudServiceError("Download cancelled")

            self._task_model.progress = TaskProgress(
                current=attempt - 1,
                total=_DOWNLOAD_RETRY_ATTEMPTS,
                message=f"Fetching cloud project info ({attempt}/{_DOWNLOAD_RETRY_ATTEMPTS})",
            )

            try:
                download_info = await asyncio.to_thread(get_project_info, remote_name, token)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[%s] Failed to fetch project info on attempt %s: %s",
                    self.id,
                    attempt,
                    exc,
                )
                download_info = None

            if download_info:
                dlink = download_info.get("dlink")
                status = str(download_info.get("status", "")).lower()
                if dlink:
                    if status and status not in {"finished", "done", "complete"}:
                        logger.warning(
                            "[%s] Download link present but project status is '%s'",
                            self.id,
                            status,
                        )
                    break

            if attempt < _DOWNLOAD_RETRY_ATTEMPTS:
                logger.info(
                    "[%s] No download link yet for '%s'. Retrying in %ss...",
                    self.id,
                    remote_name,
                    _DOWNLOAD_RETRY_DELAY_SECONDS,
                )
                await asyncio.sleep(_DOWNLOAD_RETRY_DELAY_SECONDS)

        if not dlink:
            raise CloudServiceError(
                "Cloud project is not ready for download yet. Try again later."
            )

        self._task_model.progress = TaskProgress(
            current=0,
            total=1,
            message="Preparing download",
        )

        archive_path: Path | None = None
        bytes_downloaded = 0
        total_bytes = 0
        try:
            archive_path, bytes_downloaded, total_bytes = await self._download_archive(
                dlink,
                download_info or {},
            )

            await asyncio.to_thread(project_manager.add_download, project_name, str(archive_path))

            result = CloudDownloadResult(
                project=remote_name,
                archive_size_bytes=total_bytes,
                bytes_downloaded=bytes_downloaded,
                download_info=download_info or {},
            )
            self._task_model.result = result
            self._task_model.progress = TaskProgress(
                current=1,
                total=1,
                message="Download completed",
            )
            return result
        finally:
            if archive_path is not None and archive_path.exists():
                try:
                    archive_path.unlink()
                except OSError as exc:  # noqa: BLE001
                    logger.warning(
                        "[%s] Failed to delete temporary archive %s: %s",
                        self.id,
                        archive_path,
                        exc,
                    )

    async def _download_archive(
        self,
        dlink: str,
        download_info: dict[str, Any],
    ) -> tuple[Path, int, int]:
        """Stream the remote archive to a temporary file, reporting progress."""

        try:
            url = _select_download_url(dlink, download_info)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[%s] Failed to derive direct download link from %s: %s. Falling back to original link.",
                self.id,
                dlink,
                exc,
            )
            url = dlink

        response = await asyncio.to_thread(
            requests.get,
            url,
            stream=True,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()

        total_bytes = int(response.headers.get("Content-Length", "0") or 0)
        chunk_iter = response.iter_content(chunk_size=_DOWNLOAD_CHUNK_SIZE)

        with NamedTemporaryFile(delete=False, suffix=".zip") as temp_file:
            temp_path = Path(temp_file.name)

        downloaded = 0
        try:
            with temp_path.open("wb") as destination:
                while True:
                    await self.wait_for_pause()
                    if self.is_cancelled():
                        raise CloudServiceError("Download cancelled")

                    chunk = await asyncio.to_thread(next, chunk_iter, None)
                    if chunk is None:
                        break
                    if not chunk:
                        continue

                    await asyncio.to_thread(destination.write, chunk)
                    downloaded += len(chunk)

                    total_for_progress = total_bytes or max(downloaded, 1)
                    self._update_progress(
                        downloaded,
                        total_for_progress,
                        f"Downloading archive ({downloaded}/{total_for_progress} bytes)",
                    )
        except Exception:
            temp_path.unlink(missing_ok=True)
            response.close()
            raise
        finally:
            response.close()

        return temp_path, downloaded, total_bytes


def _generate_remote_project_name(local_name: str) -> str:
    """Generate a unique, URL-safe remote project name ending with .zip."""

    safe_local = re.sub(r"[^A-Za-z0-9_-]", "_", local_name).strip("_") or "project"
    timestamp = int(time.time() * 1000)
    return f"{safe_local}-{timestamp}.zip"


def _select_download_url(dlink: str, download_info: dict[str, Any]) -> str:
    """Prefer direct download URLs embedded in cloud responses."""

    candidate = _resolve_dropbox_link(dlink)
    if candidate:
        return candidate

    info_link = download_info.get("dlink")
    if isinstance(info_link, str):
        resolved = _resolve_dropbox_link(info_link)
        if resolved:
            return resolved

    return dlink


def _resolve_dropbox_link(url: str | None) -> str | None:
    """Return a direct-download Dropbox URL when possible."""

    if not url:
        return None

    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return None

    if "openscan" in parsed.netloc:
        query = parse_qs(parsed.query)
        ids = query.get("id")
        if ids:
            return _resolve_dropbox_link(ids[0])
        return None

    if "dropbox.com" in parsed.netloc:
        query = parse_qs(parsed.query, keep_blank_values=True)
        query["dl"] = ["1"]
        new_query = urlencode([(key, value) for key, values in query.items() for value in values])
        return urlunparse(parsed._replace(query=new_query))

    return None
