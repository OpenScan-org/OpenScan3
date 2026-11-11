"""Static registry for tasks shipped with the firmware."""

from __future__ import annotations

from openscan_firmware.controllers.services.tasks.base_task import BaseTask
from openscan_firmware.controllers.services.tasks.core.cloud_task import (
    CloudDownloadTask,
    CloudUploadTask,
)
from openscan_firmware.controllers.services.tasks.core.external_trigger_run_task import (
    ExternalTriggerRunTask,
)
from openscan_firmware.controllers.services.tasks.core.focus_stacking_task import (
    FocusStackingTask,
)
from openscan_firmware.controllers.services.tasks.core.qr_scan_task import QrScanTask
from openscan_firmware.controllers.services.tasks.core.scan_analysis_task import ScanAnalysisTask
from openscan_firmware.controllers.services.tasks.core.scan_task import ScanTask


# Add new firmware task classes here. This tuple is the single source of truth
# for built-in tasks in both normal and autodiscovery startup modes.
BUILTIN_TASKS: tuple[type[BaseTask], ...] = (
    ScanTask,
    ExternalTriggerRunTask,
    FocusStackingTask,
    CloudUploadTask,
    CloudDownloadTask,
    QrScanTask,
    ScanAnalysisTask,
)
