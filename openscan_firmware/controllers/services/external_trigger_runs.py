from __future__ import annotations

import logging
from pathlib import Path

from openscan_firmware.config.external_trigger_run import ExternalTriggerRunSettings
from openscan_firmware.controllers.hardware.triggers import get_trigger_controller
from openscan_firmware.controllers.services.tasks.task_manager import get_task_manager
from openscan_firmware.models.external_trigger_run import ExternalTriggerRunPath
from openscan_firmware.models.task import Task
from openscan_firmware.utils.dir_paths import resolve_runtime_dir


logger = logging.getLogger(__name__)


RUN_STORAGE_DIRNAME = "external-trigger-runs"
PATH_FILE_NAME = "path.json"
LEGACY_MANIFEST_FILE_NAME = "manifest.json"

_run_manager_instance: "ExternalTriggerRunManager | None" = None


def _write_text_atomic(file_path: Path, payload: str) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = file_path.with_name(f".tmp_{file_path.name}")
    tmp_path.write_text(payload, encoding="utf-8")
    tmp_path.replace(file_path)


class ExternalTriggerRunManager:
    """Persistence manager for static path data of external trigger runs."""

    def __init__(self, path: str | Path | None = None):
        self._path = Path(path) if path is not None else resolve_runtime_dir(RUN_STORAGE_DIRNAME)
        self._path.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        return self._path

    def _run_dir(self, task_id: str) -> Path:
        return self._path / task_id

    def path_file(self, task_id: str) -> Path:
        return self._run_dir(task_id) / PATH_FILE_NAME

    def _legacy_manifest_file(self, task_id: str) -> Path:
        return self._run_dir(task_id) / LEGACY_MANIFEST_FILE_NAME

    def get_path_data(self, task_id: str) -> ExternalTriggerRunPath | None:
        path_file = self.path_file(task_id)
        if path_file.exists():
            return ExternalTriggerRunPath.model_validate_json(path_file.read_text(encoding="utf-8"))

        legacy_manifest_file = self._legacy_manifest_file(task_id)
        if not legacy_manifest_file.exists():
            return None
        return ExternalTriggerRunPath.model_validate_json(legacy_manifest_file.read_text(encoding="utf-8"))

    def save_path_data(self, path_data: ExternalTriggerRunPath | dict) -> ExternalTriggerRunPath:
        if not isinstance(path_data, ExternalTriggerRunPath):
            path_data = ExternalTriggerRunPath.model_validate(path_data)
        _write_text_atomic(self.path_file(path_data.task_id), path_data.model_dump_json(indent=2))
        return path_data


def get_external_trigger_run_manager(path: str | Path | None = None) -> ExternalTriggerRunManager:
    global _run_manager_instance

    if path is not None:
        return ExternalTriggerRunManager(path=path)

    if _run_manager_instance is None:
        _run_manager_instance = ExternalTriggerRunManager()
    return _run_manager_instance


def reset_external_trigger_run_manager() -> None:
    global _run_manager_instance
    _run_manager_instance = None


def _is_external_trigger_task(task: Task) -> bool:
    return task.name == "external_trigger_run_task" or task.task_type == "external_trigger_run_task"


def get_external_trigger_task(task_id: str) -> Task | None:
    task = get_task_manager().get_task_info(task_id)
    if task is None or not _is_external_trigger_task(task):
        return None
    return task


def list_external_trigger_tasks() -> list[Task]:
    tasks = [task for task in get_task_manager().get_all_tasks_info() if _is_external_trigger_task(task)]
    return sorted(tasks, key=lambda task: task.created_at, reverse=True)


async def start_external_trigger_run(
    *,
    settings: ExternalTriggerRunSettings,
    label: str | None = None,
    description: str | None = None,
    start_from_step: int = 0,
) -> Task:
    get_trigger_controller(settings.trigger_name)
    return await get_task_manager().create_and_run_task(
        "external_trigger_run_task",
        settings.model_dump(mode="json"),
        label=label,
        description=description,
        start_from_step=start_from_step,
    )


async def cancel_external_trigger_run(task_id: str) -> Task | None:
    return await get_task_manager().cancel_task(task_id)


async def pause_external_trigger_run(task_id: str) -> Task | None:
    return await get_task_manager().pause_task(task_id)


async def resume_external_trigger_run(task_id: str) -> Task | None:
    return await get_task_manager().resume_task(task_id)
