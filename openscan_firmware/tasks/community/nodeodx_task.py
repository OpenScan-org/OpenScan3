"""Small example task that sends an OpenScan project to a NodeODX server."""

from __future__ import annotations

import asyncio
from contextlib import ExitStack
from pathlib import Path
from typing import AsyncGenerator

import requests

from openscan_firmware.controllers.services.projects import get_project_manager
from openscan_firmware.controllers.services.tasks.base_task import BaseTask
from openscan_firmware.models.task import TaskProgress


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}
STATUS_FAILED = 30
STATUS_COMPLETED = 40
STATUS_CANCELLED = 50


class NodeodxReconstructionTask(BaseTask):
    """Upload all project images and wait for NodeODX to reconstruct them."""

    task_name = "nodeodx_reconstruction_task"
    task_category = "community"
    is_exclusive = False
    is_blocking = False

    async def run(
        self,
        project_name: str,
        server_url: str = "http://localhost:3000",
        poll_interval: float = 2.0,
    ) -> AsyncGenerator[TaskProgress, None]:
        project = get_project_manager().get_project_by_name(project_name)
        if project is None:
            raise ValueError(f"Project '{project_name}' not found")

        images = sorted(
            path
            for path in project.path_obj.rglob("*")
            if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
        )
        if len(images) < 2:
            raise ValueError("NodeODX needs at least two project images")

        yield TaskProgress(current=0, total=100, message="Uploading images to NodeODX")

        task_info = await asyncio.to_thread(
            self._create_remote_task,
            server_url,
            project_name,
            images,
        )
        task_uuid = task_info["uuid"]

        while True:
            info = await asyncio.to_thread(
                self._get_remote_task,
                server_url,
                task_uuid,
            )
            status_code = info["status"]["code"]
            progress = float(info.get("progress", 0))

            yield TaskProgress(
                current=progress,
                total=100,
                message=f"NodeODX reconstruction: {progress:.0f}%",
            )

            if status_code == STATUS_COMPLETED:
                self._task_model.result = {
                    "uuid": task_uuid,
                    "download_url": (
                        f"{server_url.rstrip('/')}/task/{task_uuid}/download/all.zip"
                    ),
                }
                return
            if status_code in {STATUS_FAILED, STATUS_CANCELLED}:
                raise RuntimeError(f"NodeODX task ended with status {status_code}")

            await asyncio.sleep(poll_interval)

    @staticmethod
    def _create_remote_task(
        server_url: str,
        project_name: str,
        images: list[Path],
    ) -> dict:
        """Use NodeODX's simple multipart endpoint to start a reconstruction."""
        with ExitStack() as stack:
            files = [
                ("images", (path.name, stack.enter_context(path.open("rb"))))
                for path in images
            ]
            response = requests.post(
                f"{server_url.rstrip('/')}/task/new",
                data={"name": project_name},
                files=files,
                timeout=300,
            )
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _get_remote_task(server_url: str, task_uuid: str) -> dict:
        response = requests.get(
            f"{server_url.rstrip('/')}/task/{task_uuid}/info",
            timeout=30,
        )
        response.raise_for_status()
        return response.json()
