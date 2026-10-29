"""Result and measurement builders for scan analysis tasks."""
from __future__ import annotations

from typing import Any

from openscan_firmware.config.camera import CameraSettings


def build_scan_analysis_result(
    analysis: dict[str, Any],
    *,
    position_camera_settings: list[CameraSettings] | None = None,
    previous_camera_settings: CameraSettings | None = None,
    initial_camera_settings: CameraSettings | None = None,
    sift_analysis: dict[str, Any] | None = None,
    sift_benchmark_paths: dict[str, str] | None = None,
    focus_analysis: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the persisted result for a scan analysis task."""
    result: dict[str, Any] = {
        "analysis_type": "scan_analysis",
        "crop_recommendation": analysis.get("crop_recommendation"),
        "position_analysis": analysis["position_analysis"],
    }
    if previous_camera_settings is not None or initial_camera_settings is not None:
        base_settings = initial_camera_settings or previous_camera_settings
        assert base_settings is not None
        recommended_settings = base_settings.model_copy(deep=True)
        setting_updates: dict[str, Any] = {}

        position_shutters = [
            float(settings.shutter)
            for settings in position_camera_settings or []
            if settings.shutter is not None
        ]
        if position_shutters:
            setting_updates["shutter"] = sum(position_shutters) / len(position_shutters)
        if position_camera_settings:
            setting_updates["gain"] = 1.0

        # Automatic crop application is intentionally disabled. The frontend
        # owns the explicit crop and the task only recommends camera exposure.

        if setting_updates:
            recommended_settings = recommended_settings.model_copy(update=setting_updates)
        result["camera_settings"] = {
            "previous": (
                previous_camera_settings or base_settings
            ).model_dump(mode="json"),
            "recommended": recommended_settings.model_dump(mode="json"),
            "applied": False,
        }
    if sift_analysis is not None:
        result["sift_analysis"] = sift_analysis
    if sift_benchmark_paths is not None:
        result["sift_benchmark"] = sift_benchmark_paths
    if focus_analysis is not None:
        result["focus_analysis"] = focus_analysis
    if "debug_images" in analysis:
        result["debug_images"] = analysis["debug_images"]
    return result
