"""Core task for collecting camera frames for scan setup analysis."""
from __future__ import annotations

import asyncio
import logging
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncGenerator

import numpy as np

from openscan_firmware.config.camera import CameraSettings
from openscan_firmware.controllers.services.tasks.base_task import BaseTask
from openscan_firmware.controllers.services.tasks.core.scan_analysis_report import (
    build_scan_analysis_result,
)
from openscan_firmware.models.paths import PolarPoint3D
from openscan_firmware.models.scan_analysis import NormalizedCrop
from openscan_firmware.models.task import TaskProgress
from openscan_firmware.utils.photos.analysis import (
    AnalysisFrame,
    analyze_sift_pair,
    analyze_cropping_roi,
    analyze_focus_series,
    crop_normalized_image,
    prepare_analysis_frame,
)

logger = logging.getLogger(__name__)

DEFAULT_TURNTABLE_OFFSETS = (0.0, 120.0, -120.0)
DEFAULT_FOCUS_VALUES = (10.0, 12.0, 15.0)
DEFAULT_SIFT_GRID_ROWS = 4
DEFAULT_SIFT_GRID_COLUMNS = 4
ANALYSIS_EXPOSURE_WARMUP_FRAMES = 12
ANALYSIS_EXPOSURE_STABLE_FRAMES = 2
ANALYSIS_EXPOSURE_TIMEOUT_S = 8.0
ANALYSIS_EXPOSURE_RELATIVE_TOLERANCE = 0.01


@dataclass
class ScanAnalysisContext:
    """Shared hardware and progress state for one scan analysis run."""

    camera: Any
    camera_settings: Any
    motors: Any
    original_position: PolarPoint3D
    settle_time_s: float
    calibrate_awb: Any
    calibrate_exposure: Any
    set_extended_debug: Any
    calibration_available: bool
    initial_calibration_steps: int
    per_position_calibration_steps: int
    sift_steps: int
    total_captures: int
    position_count: int
    total_work: int
    crop: NormalizedCrop | None


@dataclass
class ScanAnalysisMeasurements:
    """Frames and camera snapshots collected during one analysis run."""

    original_camera_settings: CameraSettings | None
    debug_images: bool
    initial_camera_settings: CameraSettings | None = None
    initial_calibration: dict[str, Any] = field(default_factory=dict)
    position_camera_settings: list[CameraSettings] = field(default_factory=list)
    focus_series: dict[str, list[AnalysisFrame]] = field(default_factory=dict)
    captured_frames: list[tuple[AnalysisFrame, dict[str, Any]]] = field(
        default_factory=list
    )
    focus_reports: list[dict[str, Any]] = field(default_factory=list)
    sift_analysis: dict[str, Any] | None = None
    sift_benchmark_paths: dict[str, str] | None = None


class ScanAnalysisTask(BaseTask):
    """Capture and analyze a small set of RGB frames for scan setup."""

    task_name = "scan_analysis_task"
    task_category = "core"
    is_exclusive = True

    async def run(
        self,
        camera_name: str,
        crop: NormalizedCrop | list[float] | None = None,
        turntable_offsets: list[float] | None = None,
        settle_time_s: float = 0.25,
        focus_values: list[float] | None = None,
        debug_images: bool = False,
        sift_turntable_angle: float = 5.0,
        sift_benchmark: bool = False,
        sift_max_dimension: int = 960,
        sift_grid_rows: int = DEFAULT_SIFT_GRID_ROWS,
        sift_grid_columns: int = DEFAULT_SIFT_GRID_COLUMNS,
    ) -> AsyncGenerator[TaskProgress, None]:
        """Capture RGB arrays at relative turntable positions."""
        normalized_crop = (
            NormalizedCrop.model_validate(crop)
            if crop is not None
            else None
        )
        offsets = list(DEFAULT_TURNTABLE_OFFSETS if turntable_offsets is None else turntable_offsets)
        focus_values = list(DEFAULT_FOCUS_VALUES if not focus_values else focus_values)
        focus_values = [float(value) for value in focus_values]
        if not 0 < sift_turntable_angle <= 45:
            raise ValueError("sift_turntable_angle must be greater than 0 and at most 45 degrees.")
        if sift_max_dimension not in (640, 960, 1280):
            raise ValueError("sift_max_dimension must be one of 640, 960, or 1280.")
        if sift_grid_rows <= 0 or sift_grid_columns <= 0:
            raise ValueError("sift_grid_rows and sift_grid_columns must be positive.")

        capture_plan = [("autofocus", None)]
        capture_plan.extend(("manual", value) for value in focus_values)

        # Keep hardware imports inside run so task discovery has no side effects.
        from openscan_firmware.controllers.hardware import motors
        from openscan_firmware.controllers.hardware.cameras.camera import get_camera_controller

        camera = get_camera_controller(camera_name)
        rotor = motors.get_motor_controller("rotor")
        turntable = motors.get_motor_controller("turntable")
        original_position = PolarPoint3D(
            theta=float(rotor.model.angle),
            fi=float(turntable.model.angle),
            r=1.0,
        )

        camera_settings = getattr(camera, "settings", None)
        original_camera_settings = None
        if camera_settings is not None and hasattr(camera_settings, "model"):
            original_camera_settings = camera_settings.model.model_copy(deep=True)

        total_captures = len(offsets) * len(capture_plan)
        calibrate_awb = getattr(camera, "calibrate_awb_and_lock", None)
        calibrate_exposure = getattr(camera, "calibrate_exposure_and_lock", None)
        calibration_available = callable(calibrate_awb) and callable(calibrate_exposure)
        set_extended_debug = getattr(camera, "set_extended_debug_enabled", None)
        extended_debug_was_enabled = False
        get_extended_debug_status = getattr(camera, "extended_debug_status", None)
        if callable(get_extended_debug_status):
            extended_debug_was_enabled = bool(
                get_extended_debug_status().get("enabled", False)
            )
        initial_calibration_steps = 1 if calibration_available else 0
        per_position_calibration_steps = 1 if calibration_available else 0
        sift_steps = 3 if calibration_available else 0
        total_work = (
            total_captures
            + initial_calibration_steps
            + sift_steps
            + len(offsets) * per_position_calibration_steps
            + 1
        )
        context = ScanAnalysisContext(
            camera=camera,
            camera_settings=camera_settings,
            motors=motors,
            original_position=original_position,
            settle_time_s=settle_time_s,
            calibrate_awb=calibrate_awb,
            calibrate_exposure=calibrate_exposure,
            set_extended_debug=set_extended_debug,
            calibration_available=calibration_available,
            initial_calibration_steps=initial_calibration_steps,
            per_position_calibration_steps=per_position_calibration_steps,
            sift_steps=sift_steps,
            total_captures=total_captures,
            position_count=len(offsets),
            total_work=total_work,
            crop=normalized_crop,
        )
        # Only small analysis images are kept between captures. Raw camera arrays
        # are released before the next capture starts.
        measurements = ScanAnalysisMeasurements(
            original_camera_settings=original_camera_settings,
            debug_images=debug_images,
            initial_camera_settings=original_camera_settings,
        )
        task_succeeded = False

        try:
            async for progress in self._prepare_analysis_camera(
                context=context,
                measurements=measurements,
            ):
                yield progress
            if self.is_cancelled():
                return

            async for progress in self._capture_focus_series(
                context=context,
                offsets=offsets,
                capture_plan=capture_plan,
                measurements=measurements,
            ):
                yield progress
            if self.is_cancelled():
                return

            result = self._build_analysis_result(context, measurements)
            await self._apply_recommended_settings(camera_settings, result)

            if context.calibration_available:
                await _set_extended_debug(context.set_extended_debug, True)
                sift_result: dict[str, Any] = {}
                async for progress in self._capture_sift_probe(
                    context=context,
                    sift_turntable_angle=sift_turntable_angle,
                    sift_max_dimension=sift_max_dimension,
                    sift_grid_rows=sift_grid_rows,
                    sift_grid_columns=sift_grid_columns,
                    sift_benchmark=sift_benchmark,
                    result=sift_result,
                ):
                    yield progress
                measurements.sift_analysis = sift_result.get("analysis")
                measurements.sift_benchmark_paths = sift_result.get("benchmark_paths")
                if measurements.sift_analysis is not None:
                    result["sift_analysis"] = measurements.sift_analysis
                if measurements.sift_benchmark_paths is not None:
                    result["sift_benchmark"] = measurements.sift_benchmark_paths
            if self.is_cancelled():
                return

            self._task_model.result = result
            task_succeeded = True
            yield TaskProgress(
                current=total_work,
                total=total_work,
                message="Analysis frames captured.",
            )
        finally:
            if not task_succeeded and measurements.original_camera_settings is not None:
                try:
                    await _restore_camera_settings(
                        camera_settings,
                        measurements.original_camera_settings,
                    )
                except Exception:
                    logger.exception("Failed to restore camera settings after scan analysis.")
            try:
                await motors.move_to_point(original_position)
            except Exception:
                logger.exception("Failed to restore motor position after scan analysis.")
            if callable(set_extended_debug):
                try:
                    await asyncio.to_thread(set_extended_debug, extended_debug_was_enabled)
                except Exception:
                    logger.exception("Failed to restore extended camera debug state.")

    async def _prepare_analysis_camera(
        self,
        context: ScanAnalysisContext,
        measurements: ScanAnalysisMeasurements,
    ) -> AsyncGenerator[TaskProgress, None]:
        """Calibrate the initial camera state for the analysis run."""
        if not context.calibration_available:
            await _set_extended_debug(context.set_extended_debug, True)
            return

        async for progress in self._calibrate_initial_camera(
            context=context,
            result=measurements.initial_calibration,
        ):
            yield progress
        measurements.initial_camera_settings = measurements.initial_calibration.get(
            "camera_settings",
            measurements.original_camera_settings,
        )

        await _set_extended_debug(context.set_extended_debug, True)

    def _build_analysis_result(
        self,
        context: ScanAnalysisContext,
        measurements: ScanAnalysisMeasurements,
    ) -> dict[str, Any]:
        """Analyze collected frames and assemble the public task result."""
        # Automatic crop recommendation is intentionally disabled. The
        # frontend supplies the crop used for analysis when available.
        # Re-enable ``recommend_crop=True`` here when automatic cropping
        # becomes useful again.
        analysis = analyze_cropping_roi(
            measurements.focus_series,
            captured_frames=(
                measurements.captured_frames if measurements.debug_images else None
            ),
            recommend_crop=False,
        )
        analysis["crop_recommendation"] = None
        focus_analysis = analyze_focus_series(
            measurements.focus_reports,
            crop_recommendation=(
                {"rect_normalized": list(context.crop.root)}
                if context.crop is not None
                else None
            ),
        )
        return build_scan_analysis_result(
            analysis=analysis,
            position_camera_settings=measurements.position_camera_settings,
            previous_camera_settings=measurements.original_camera_settings,
            initial_camera_settings=measurements.initial_camera_settings,
            sift_analysis=measurements.sift_analysis,
            sift_benchmark_paths=measurements.sift_benchmark_paths,
            focus_analysis=focus_analysis,
        )

    async def _apply_recommended_settings(
        self,
        camera_settings: Any,
        result: dict[str, Any],
    ) -> None:
        """Keep the recommended settings active after a successful run."""
        if camera_settings is None or "camera_settings" not in result:
            return
        recommended_settings = CameraSettings.model_validate(
            result["camera_settings"]["recommended"]
        )
        await asyncio.to_thread(camera_settings.replace, recommended_settings)
        result["camera_settings"]["applied"] = True

    async def _calibrate_initial_camera(
        self,
        context: ScanAnalysisContext,
        result: dict[str, Any],
    ) -> AsyncGenerator[TaskProgress, None]:
        """Calibrate the camera once and return its initial settings snapshot."""
        initial_settings = await _calibrate_analysis_camera_settings(
            calibrate_awb=context.calibrate_awb,
            calibrate_exposure=context.calibrate_exposure,
            camera_settings=context.camera_settings,
            include_awb=True,
        )
        if initial_settings is not None:
            result["camera_settings"] = initial_settings
        yield TaskProgress(
            current=1,
            total=context.total_work,
            message="Camera settings calibrated and locked.",
        )

    async def _capture_sift_probe(
        self,
        context: ScanAnalysisContext,
        sift_turntable_angle: float,
        sift_max_dimension: int,
        sift_grid_rows: int,
        sift_grid_columns: int,
        sift_benchmark: bool,
        result: dict[str, Any],
    ) -> AsyncGenerator[TaskProgress, None]:
        """Capture, compare, and report the two SIFT probe frames."""
        await self.wait_for_pause()
        if self.is_cancelled():
            return
        await context.motors.move_to_point(context.original_position)
        if context.settle_time_s:
            await asyncio.sleep(context.settle_time_s)

        sift_focus_mode, sift_focus = _sift_focus_plan(context.camera_settings)
        first_sift_path = None
        second_sift_path = None
        if sift_benchmark:
            benchmark_root = Path(tempfile.gettempdir())
            first_sift_path = benchmark_root / f"openscan-sift-{self.id}-first.npy"
            second_sift_path = benchmark_root / f"openscan-sift-{self.id}-second.npy"
            result["benchmark_paths"] = {
                "first_image": str(first_sift_path),
                "second_image": str(second_sift_path),
            }

        first_sift_frame, _ = await self._capture_frame(
            context=context,
            position=context.original_position,
            index=-1,
            capture_index=0,
            offset=0.0,
            focus_mode=sift_focus_mode,
            requested_focus=sift_focus,
            analysis_max_dimension=sift_max_dimension,
            raw_image_path=first_sift_path,
        )
        first_sift_image = _prepare_task_sift_image(first_sift_frame, context.crop)
        sift_progress_start = (
            context.initial_calibration_steps
            + context.total_captures
            + context.per_position_calibration_steps * context.position_count
        )
        yield TaskProgress(
            current=sift_progress_start + 1,
            total=context.total_work,
            message="Captured first SIFT comparison frame.",
        )

        if sift_focus_mode == "autofocus":
            actual_focus = first_sift_frame.camera_metadata.raw_metadata.get(
                "LensPosition"
            )
            if actual_focus is not None:
                sift_focus_mode = "manual"
                sift_focus = float(actual_focus)
                await _update_focus(
                    context.camera_settings,
                    autofocus=False,
                    focus=sift_focus,
                )

        sift_position = PolarPoint3D(
            theta=context.original_position.theta,
            fi=context.original_position.fi + sift_turntable_angle,
            r=1.0,
        )
        await context.motors.move_to_point(sift_position)
        if context.settle_time_s:
            await asyncio.sleep(context.settle_time_s)

        second_sift_frame, _ = await self._capture_frame(
            context=context,
            position=sift_position,
            index=-1,
            capture_index=1,
            offset=sift_turntable_angle,
            focus_mode=sift_focus_mode,
            requested_focus=sift_focus,
            analysis_max_dimension=sift_max_dimension,
            raw_image_path=second_sift_path,
        )
        second_sift_image = _prepare_task_sift_image(second_sift_frame, context.crop)
        yield TaskProgress(
            current=sift_progress_start + 2,
            total=context.total_work,
            message="Captured second SIFT comparison frame.",
        )

        sift_analysis = analyze_sift_pair(
            first_sift_image,
            second_sift_image,
            grid_rows=sift_grid_rows,
            grid_columns=sift_grid_columns,
        )
        sift_analysis["turntable_angle"] = float(sift_turntable_angle)
        result["analysis"] = sift_analysis
        yield TaskProgress(
            current=sift_progress_start + 3,
            total=context.total_work,
            message="SIFT feature matching completed.",
        )

    async def _capture_focus_series(
        self,
        context: ScanAnalysisContext,
        offsets: list[float],
        capture_plan: list[tuple[str, float | None]],
        measurements: ScanAnalysisMeasurements,
    ) -> AsyncGenerator[TaskProgress, None]:
        """Capture the focus series at each configured turntable position."""
        for index, offset in enumerate(offsets):
            await self.wait_for_pause()
            if self.is_cancelled():
                return

            position = PolarPoint3D(
                theta=context.original_position.theta,
                fi=context.original_position.fi + float(offset),
                r=1.0,
            )
            await context.motors.move_to_point(position)
            if context.settle_time_s:
                await asyncio.sleep(context.settle_time_s)

            if context.calibration_available:
                if callable(context.set_extended_debug):
                    await asyncio.to_thread(context.set_extended_debug, False)
                try:
                    position_settings = await _calibrate_analysis_camera_settings(
                        calibrate_awb=context.calibrate_awb,
                        calibrate_exposure=context.calibrate_exposure,
                        camera_settings=context.camera_settings,
                        include_awb=False,
                    )
                finally:
                    if callable(context.set_extended_debug):
                        await asyncio.to_thread(context.set_extended_debug, True)
                if position_settings is not None:
                    measurements.position_camera_settings.append(position_settings)
                yield TaskProgress(
                    current=(
                        context.initial_calibration_steps
                        + index * (
                            len(capture_plan) + context.per_position_calibration_steps
                        )
                        + 1
                    ),
                    total=context.total_work,
                    message=(
                        f"Camera settings calibrated at turntable position {index + 1} "
                        f"of {len(offsets)}."
                    ),
                )

            for capture_index, (focus_mode, requested_focus) in enumerate(capture_plan):
                await self.wait_for_pause()
                if self.is_cancelled():
                    return

                frame, frame_report = await self._capture_frame(
                    context=context,
                    position=position,
                    index=index,
                    capture_index=capture_index,
                    offset=offset,
                    focus_mode=focus_mode,
                    requested_focus=requested_focus,
                )

                if measurements.debug_images:
                    measurements.captured_frames.append((frame, frame_report))
                measurements.focus_reports.append(frame_report)
                series_name = (
                    "autofocus"
                    if focus_mode == "autofocus"
                    else str(requested_focus)
                )
                measurements.focus_series.setdefault(series_name, []).append(frame)

                current = (
                    context.initial_calibration_steps
                    + index * (
                        len(capture_plan) + context.per_position_calibration_steps
                    )
                    + context.per_position_calibration_steps
                    + capture_index
                    + 1
                )
                yield TaskProgress(
                    current=current,
                    total=context.total_work,
                    message=(
                        f"Captured analysis frame "
                        f"{index * len(capture_plan) + capture_index + 1} "
                        f"of {context.total_captures}."
                    ),
                )

    async def _capture_frame(
        self,
        context: ScanAnalysisContext,
        position: PolarPoint3D,
        index: int,
        capture_index: int,
        offset: float,
        focus_mode: str,
        requested_focus: float | None,
        analysis_max_dimension: int = 640,
        raw_image_path: Path | None = None,
    ) -> tuple[AnalysisFrame, dict[str, Any]]:
        """Apply one focus setting, capture a frame, and build its report."""
        if focus_mode == "autofocus":
            await _update_focus(context.camera_settings, autofocus=True)
        elif focus_mode == "manual":
            await _update_focus(
                context.camera_settings,
                autofocus=False,
                focus=requested_focus,
            )

        photo = await context.camera.photo_async(image_format="rgb_array")
        try:
            if raw_image_path is not None:
                if not isinstance(photo.data, np.ndarray):
                    raise ValueError("SIFT benchmark frames require an RGB NumPy array.")
                np.save(raw_image_path, photo.data)
            frame = prepare_analysis_frame(
                photo,
                position,
                index,
                max_dimension=analysis_max_dimension,
            )
            metadata = frame.camera_metadata.model_dump(mode="json")
            raw_metadata = metadata.get("raw_metadata", {})
            frame_report = {
                "index": index,
                "capture_index": capture_index,
                "turntable_offset": float(offset),
                "turntable_angle": float(position.fi % 360),
                "focus_mode": focus_mode,
                "requested_focus": requested_focus,
                "actual_lens_position": raw_metadata.get("LensPosition"),
                "focus_fom": raw_metadata.get("FocusFoM"),
                "camera_metadata": metadata,
            }
            return frame, frame_report
        finally:
            del photo

async def _calibrate_analysis_camera_settings(
    calibrate_awb: Any,
    calibrate_exposure: Any,
    camera_settings: Any,
    include_awb: bool,
) -> CameraSettings | None:
    """Run the camera calibrations needed by scan analysis and return a snapshot."""
    if include_awb:
        await asyncio.to_thread(calibrate_awb)
    await asyncio.to_thread(
        calibrate_exposure,
        warmup_frames=ANALYSIS_EXPOSURE_WARMUP_FRAMES,
        stable_frames=ANALYSIS_EXPOSURE_STABLE_FRAMES,
        timeout_s=ANALYSIS_EXPOSURE_TIMEOUT_S,
        use_photo_config=True,
        relative_tolerance=ANALYSIS_EXPOSURE_RELATIVE_TOLERANCE,
    )
    if camera_settings is None or not hasattr(camera_settings, "model"):
        return None
    return camera_settings.model.model_copy(deep=True)


async def _set_extended_debug(set_extended_debug: Any, enabled: bool) -> None:
    """Set extended camera debug state when the controller supports it."""
    if not callable(set_extended_debug):
        return
    if not await asyncio.to_thread(set_extended_debug, enabled):
        logger.warning(
            "Extended camera debug could not be %s for scan analysis.",
            "enabled" if enabled else "restored",
        )


async def _update_focus(settings: Any, autofocus: bool, focus: float | None = None) -> None:
    """Apply one focus mode without blocking the task event loop."""
    if settings is None:
        raise ValueError("The camera does not expose settings for focus analysis.")
    values: dict[str, Any] = {"AF": autofocus}
    if focus is not None:
        values["manual_focus"] = focus
    await asyncio.to_thread(settings.update, **values)


def _sift_focus_plan(camera_settings: Any) -> tuple[str, float | None]:
    """Select one focus mode for the two SIFT frames."""
    settings_model = getattr(camera_settings, "model", None)
    if settings_model is None or getattr(settings_model, "AF", True):
        return "autofocus", None
    manual_focus = getattr(settings_model, "manual_focus", None)
    if manual_focus is None:
        raise ValueError("Manual focus is required for the SIFT comparison.")
    return "manual", float(manual_focus)


def _prepare_task_sift_image(
    frame: AnalysisFrame,
    crop: NormalizedCrop | None,
) -> np.ndarray:
    """Prepare a SIFT image using the explicit task crop when provided."""
    if crop is None:
        return frame.analysis_image
    return crop_normalized_image(frame.analysis_image, crop.as_tuple)


async def _restore_camera_settings(settings: Any, original_settings: Any) -> None:
    """Restore camera settings when the analysis did not complete successfully."""
    await asyncio.to_thread(settings.replace, original_settings)
