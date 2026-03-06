"""Background tasks for interacting with the OpenScan Cloud."""

from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import re
import time
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, AsyncGenerator

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
_UPLOAD_STREAM_CHUNK_SIZE = 512 * 1024


class CloudUploadTask(BaseTask):
    """Upload an existing project directory to the OpenScan Cloud."""

    task_name = "cloud_upload_task"
    task_category = "core"
    is_exclusive = False
    is_blocking = False

    async def run(
        self,
        project_name: str,
        token: str | None = None,
    ) -> AsyncGenerator[TaskProgress, None]:
        """Stream the upload and emit byte-level TaskProgress updates."""

        settings = _require_cloud_settings()
        project_manager = get_project_manager()
        project = project_manager.get_project_by_name(project_name)
        if project is None:
            raise CloudServiceError(f"Project '{project_name}' not found")

        logger.info("[%s] Preparing archive for project '%s'", self.id, project.name)

        preparing_progress = TaskProgress(
            current=0,
            total=1,
            message="Preparing upload: creating archive",
        )
        self._task_model.progress = preparing_progress
        yield preparing_progress

        archive_started = time.perf_counter()
        archive_file, archive_size = await asyncio.to_thread(_build_project_archive, project)
        archive_duration = time.perf_counter() - archive_started
        photo_count = await asyncio.to_thread(_count_project_photos, project)
        logger.info(
            "[%s] Archive ready for '%s' (size=%s bytes, photos=%s) after %.2fs",
            self.id,
            project.name,
            archive_size,
            photo_count,
            archive_duration,
        )
        parts_required = max(1, (archive_size + settings.split_size - 1) // settings.split_size)
        total_bytes = max(int(archive_size), 1)

        remote_project_name = _generate_remote_project_name(project.name)

        logger.info(
            "[%s] Uploading project '%s' with %s photos (%s bytes) split into %s part(s)",
            self.id,
            project.name,
            photo_count,
            archive_size,
            parts_required,
        )

        try:
            logger.info(
                "[%s] Requesting cloud upload slots for '%s' (%s parts, split size=%s)",
                self.id,
                project.name,
                parts_required,
                settings.split_size,
            )
            create_response = await asyncio.to_thread(
                _create_project,
                remote_project_name,
                photos=photo_count,
                filesize=archive_size,
                parts=parts_required,
                token=token,
            )
            logger.info(
                "[%s] Received %s upload links for '%s'",
                self.id,
                len(create_response.get("ulink", []) if isinstance(create_response.get("ulink"), list) else []),
                project.name,
            )

            upload_links = create_response.get("ulink")
            if not isinstance(upload_links, list) or len(upload_links) != parts_required:
                raise CloudServiceError(
                    f"Unexpected upload link information for project '{project.name}'"
                )

            chunk_iterator = _iter_chunks(archive_file, settings.split_size)
            uploaded_bytes = 0
            loop = asyncio.get_running_loop()

            start_progress = TaskProgress(
                current=0,
                total=total_bytes,
                message="Preparing upload",
            )
            self._task_model.progress = start_progress
            yield start_progress

            for index in range(1, parts_required + 1):
                await self.wait_for_pause()
                if self.is_cancelled():
                    logger.warning("[%s] Upload cancelled at part %s", self.id, index)
                    raise CloudServiceError("Upload cancelled")

                chunk = await asyncio.to_thread(next, chunk_iterator, None)
                if chunk is None:
                    raise CloudServiceError("Upload aborted: missing chunk data")

                chunk.seek(0, io.SEEK_END)
                chunk_size = chunk.tell()
                chunk.seek(0)

                part_link = upload_links[index - 1]
                logger.info(
                    "[%s] Uploading part %s/%s for project '%s' (%s bytes)",
                    self.id,
                    index,
                    parts_required,
                    project.name,
                    chunk_size,
                )
                progress_queue: asyncio.Queue[int | None] = asyncio.Queue()

                def _emit_progress(sent: int) -> None:
                    loop.call_soon_threadsafe(progress_queue.put_nowait, sent)

                upload_future = asyncio.create_task(
                    asyncio.to_thread(
                        _upload_file,
                        chunk,
                        part_link,
                        progress_callback=_emit_progress,
                        stream_chunk_size=_UPLOAD_STREAM_CHUNK_SIZE,
                    )
                )

                def _signal_completion(_fut: asyncio.Future[Any]) -> None:  # noqa: ANN001
                    loop.call_soon_threadsafe(progress_queue.put_nowait, None)

                upload_future.add_done_callback(_signal_completion)

                part_started = time.perf_counter()
                while True:
                    delta = await progress_queue.get()
                    if delta is None:
                        break

                    uploaded_bytes = min(uploaded_bytes + delta, total_bytes)
                    progress = TaskProgress(
                        current=uploaded_bytes,
                        total=total_bytes,
                        message=(
                            f"Uploading archive [{index}/{parts_required} parts]"
                        ),
                    )
                    self._task_model.progress = progress
                    yield progress

                await upload_future
                logger.info(
                    "[%s] Finished part %s/%s for project '%s' in %.2fs",
                    self.id,
                    index,
                    parts_required,
                    project.name,
                    time.perf_counter() - part_started,
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

            await asyncio.to_thread(
                project_manager.mark_uploaded,
                project_name,
                True,
                remote_project_name,
            )

            completed_progress = TaskProgress(
                current=total_bytes,
                total=total_bytes,
                message="Upload completed",
            )
            self._task_model.progress = completed_progress
            yield completed_progress
            return
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
    ) -> AsyncGenerator[TaskProgress, None]:
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

            retry_progress = TaskProgress(
                current=attempt - 1,
                total=_DOWNLOAD_RETRY_ATTEMPTS,
                message=f"Fetching cloud project info ({attempt}/{_DOWNLOAD_RETRY_ATTEMPTS})",
            )
            self._task_model.progress = retry_progress
            yield retry_progress

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
                    if status and status not in {"finished", "done", "complete", "processing done"}:
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

        prepare_progress = TaskProgress(current=0, total=1, message="Preparing download")
        self._task_model.progress = prepare_progress
        yield prepare_progress

        archive_path: Path | None = None
        bytes_downloaded = 0
        total_bytes = 0
        stream_result: dict[str, Any] = {}
        stream = self._download_archive_stream(dlink, download_info or {}, stream_result)
        try:
            async with contextlib.aclosing(stream) as downloader:
                while True:
                    try:
                        downloaded, total_for_progress = await downloader.__anext__()
                    except StopAsyncIteration:
                        archive_path = stream_result.get("path")
                        bytes_downloaded = int(stream_result.get("bytes_downloaded", 0))
                        total_bytes = int(stream_result.get("total_bytes", 0))
                        break

                    progress = TaskProgress(
                        current=downloaded,
                        total=total_for_progress,
                        message=f"Downloading archive ({downloaded}/{total_for_progress} bytes)",
                    )
                    self._task_model.progress = progress
                    yield progress

            await asyncio.to_thread(project_manager.add_download, project_name, str(archive_path))

            result = CloudDownloadResult(
                project=remote_name,
                archive_size_bytes=total_bytes,
                bytes_downloaded=bytes_downloaded,
                download_info=download_info or {},
            )
            self._task_model.result = result
            completion = TaskProgress(
                current=max(bytes_downloaded, 1),
                total=max(total_bytes, 1),
                message="Download completed",
            )
            self._task_model.progress = completion
            yield completion
            return
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

    async def _download_archive_stream(
        self,
        dlink: str,
        download_info: dict[str, Any],
        stream_result: dict[str, Any],
    ) -> AsyncGenerator[tuple[int, int], None]:
        """Stream the remote archive to a temporary file, yielding byte progress."""

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
                    yield downloaded, total_for_progress
        except Exception:
            temp_path.unlink(missing_ok=True)
            response.close()
            raise
        finally:
            response.close()
        stream_result["path"] = temp_path
        stream_result["bytes_downloaded"] = downloaded
        stream_result["total_bytes"] = total_bytes


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
