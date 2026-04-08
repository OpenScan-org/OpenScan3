from __future__ import annotations

import logging
from typing import AsyncGenerator

from openscan_firmware.config.external_trigger_run import ExternalTriggerRunSettings
from openscan_firmware.controllers.hardware.triggers import TriggerController, get_trigger_controller
from openscan_firmware.controllers.services.external_trigger_runs import get_external_trigger_run_manager
from openscan_firmware.controllers.services.tasks.base_task import BaseTask
from openscan_firmware.controllers.services.tasks.core.scan_task import generate_scan_path
from openscan_firmware.models.external_trigger_run import (
    ExternalTriggerPoint,
    ExternalTriggerRunPath,
)
from openscan_firmware.models.paths import PolarPoint3D
from openscan_firmware.models.task import TaskProgress
from openscan_firmware.utils.paths.paths import polar_to_cartesian


logger = logging.getLogger(__name__)


class ExternalTriggerRunTask(BaseTask):
    """Execute a motor path while triggering an external camera over GPIO."""

    task_name = "external_trigger_run_task"
    task_category = "core"
    is_exclusive = True

    async def _cleanup_run(self, trigger: TriggerController) -> None:
        """Reset trigger state and move motors back to the default origin."""
        from openscan_firmware.controllers.hardware import motors

        try:
            await trigger.reset()
        except Exception as exc:
            logger.error("Error while resetting external trigger after run: %s", exc, exc_info=True)

        try:
            await motors.move_to_point(PolarPoint3D(90, 90))
        except Exception as exc:
            logger.error("Error while moving motors back to origin after external trigger run: %s", exc, exc_info=True)

    async def run(
        self,
        settings: ExternalTriggerRunSettings | dict,
        *,
        label: str | None = None,
        description: str | None = None,
        start_from_step: int = 0,
    ) -> AsyncGenerator[TaskProgress, None]:
        del label, description

        if not isinstance(settings, ExternalTriggerRunSettings):
            settings = ExternalTriggerRunSettings.model_validate(settings)

        manager = get_external_trigger_run_manager()
        path_dict = generate_scan_path(settings.to_scan_settings())
        total_steps = len(path_dict)

        path_data = ExternalTriggerRunPath(
            task_id=self.id,
            total_steps=total_steps,
            points=[
                ExternalTriggerPoint(
                    execution_step=execution_step,
                    original_step=original_step,
                    polar_coordinates=polar_point,
                    cartesian_coordinates=polar_to_cartesian(polar_point),
                )
                for execution_step, (polar_point, original_step) in enumerate(path_dict.items())
            ],
        )
        manager.save_path_data(path_data)
        trigger = get_trigger_controller(settings.trigger_name)
        try:
            current_step = min(int(self._task_model.progress.current), total_steps)
            resume_from_step = max(start_from_step, current_step)
            path_items = list(path_dict.items())

            from openscan_firmware.controllers.hardware import motors

            for execution_step in range(resume_from_step, total_steps):
                if self.is_cancelled():
                    yield TaskProgress(current=self._task_model.progress.current, total=total_steps, message="External trigger run cancelled.")
                    return

                await self.wait_for_pause()

                if self.is_cancelled():
                    yield TaskProgress(current=self._task_model.progress.current, total=total_steps, message="External trigger run cancelled.")
                    return

                polar_point, original_step = path_items[execution_step]
                await motors.move_to_point(polar_point)
                await trigger.trigger(
                    pre_trigger_delay_ms=settings.pre_trigger_delay_ms,
                    post_trigger_delay_ms=settings.post_trigger_delay_ms,
                )

                progress = TaskProgress(
                    current=execution_step + 1,
                    total=total_steps,
                    message="External trigger run in progress.",
                )
                self._task_model.progress = progress
                yield progress

            self._task_model.result = {
                "task_id": self.id,
                "path_path": str(manager.path_file(self.id)),
            }
            yield TaskProgress(current=total_steps, total=total_steps, message="External trigger run completed successfully.")
        except Exception as exc:
            logger.error("External trigger run %s failed: %s", self.id, exc, exc_info=True)
            raise
        finally:
            await self._cleanup_run(trigger)
