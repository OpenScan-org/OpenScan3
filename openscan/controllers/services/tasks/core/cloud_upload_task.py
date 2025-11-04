"""Core task responsible for uploading projects to the OpenScan Cloud."""

from __future__ import annotations

import asyncio
import logging
import re
import time

from openscan.controllers.services.cloud import (
    CloudServiceError,
    CloudUploadResult,
    _count_project_photos,
    _create_project,
    _iter_chunks,
    _require_cloud_settings,
    _start_project,
    _upload_file,
    _build_project_archive,
)
from openscan.controllers.services.projects import get_project_manager
from openscan.controllers.services.tasks.base_task import BaseTask
from openscan.models.task import TaskProgress

logger = logging.getLogger(__name__)


class CloudUploadTask(BaseTask):
    """Upload an existing project directory to the OpenScan Cloud."""

    task_name = "cloud_upload_task"
    task_category = "core"
    is_exclusive = False
    is_blocking = False

    async def run(self, project_name: str, token: str | None = None) -> CloudUploadResult:
        """Perform the upload asynchronously with background offloading.

        Args:
            project_name: Name of the project directory to upload.
            token: Optional cloud token override.

        Returns:
            CloudUploadResult: Summary information about the performed upload.

        Raises:
            CloudServiceError: If prerequisites are missing or the upload fails.
        """

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


def _generate_remote_project_name(local_name: str) -> str:
    """Generate a unique, URL-safe remote project name ending with .zip."""

    safe_local = re.sub(r"[^A-Za-z0-9_-]", "_", local_name).strip("_") or "project"
    timestamp = int(time.time() * 1000)
    return f"{safe_local}-{timestamp}.zip"
