"""Runtime data helpers for camera analysis tasks."""
from __future__ import annotations

import base64
import io
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
from PIL import Image

from openscan_firmware.models.camera import CameraMetadata, PhotoData
from openscan_firmware.models.paths import PolarPoint3D


@dataclass
class AnalysisFrame:
    """The small in-memory representation used by later analysis steps."""

    index: int
    position: PolarPoint3D
    camera_metadata: CameraMetadata
    analysis_image: np.ndarray


@dataclass
class _CroppingRoiMask:
    """Internal cropping ROI mask built from a turntable series."""

    background: np.ndarray
    variation: np.ndarray
    mask: np.ndarray


@dataclass(frozen=True)
class SiftQualityThresholds:
    """Initial heuristics for the qualitative SIFT report."""

    min_feature_count: int = 300
    critical_feature_count: int = 150
    min_mutual_match_count: int = 100
    critical_mutual_match_count: int = 50
    min_matchable_feature_ratio: float = 0.20
    critical_matchable_feature_ratio: float = 0.10
    min_geometric_inlier_ratio: float = 0.70
    critical_geometric_inlier_ratio: float = 0.50
    min_feature_per_cell: int = 5
    min_matchable_feature_per_cell: int = 2
    min_feature_coverage: float = 0.50
    critical_feature_coverage: float = 0.25
    min_matchable_coverage: float = 0.50
    critical_matchable_coverage: float = 0.25
    min_balance_ratio: float = 0.75
    critical_balance_ratio: float = 0.50


_CROPPING_ROI_TAIL_WIDTH_RATIO = 0.4
_CROPPING_ROI_TAIL_MIN_ROWS = 12
_CROPPING_ROI_SUPPORT_MIN_ROWS = 12
_CROPPING_ROI_SUPPORT_MIN_GROWTH = 1.15


def analyze_cropping_roi(
    focus_series: dict[str, list[AnalysisFrame]],
    *,
    captured_frames: list[tuple[AnalysisFrame, dict[str, Any]]] | None = None,
    recommend_crop: bool = True,
) -> dict[str, Any]:
    """Analyze crop suitability and object position from focus series.

    The object position is reported through vertical and horizontal imbalance
    values calculated from the cropping ROI. When ``captured_frames`` is
    provided, compact debug images are included as well. Set
    ``recommend_crop`` to false when the ROI is only needed for position
    analysis.
    """
    cropping_roi_masks = {
        focus: _detect_cropping_roi_mask(series)
        for focus, series in focus_series.items()
    }
    consensus_cropping_mask = _combine_cropping_roi_masks(cropping_roi_masks)
    object_mask = _remove_narrow_cropping_roi_tail(
        consensus_cropping_mask,
        width_ratio=_CROPPING_ROI_TAIL_WIDTH_RATIO,
        min_rows=_CROPPING_ROI_TAIL_MIN_ROWS,
    )
    support_mask = _remove_bottom_cropping_roi_support(
        object_mask,
        min_rows=_CROPPING_ROI_SUPPORT_MIN_ROWS,
        min_width_growth=_CROPPING_ROI_SUPPORT_MIN_GROWTH,
    )
    support_removed = not np.array_equal(object_mask, support_mask)
    object_mask = support_mask
    crop_recommendation = _crop_recommendation(object_mask) if recommend_crop else None
    if crop_recommendation is not None:
        crop_recommendation["support_exclusion"] = {
            "applied": support_removed,
            "method": "silhouette_transition",
        }
    position_analysis = _calculate_position_imbalance(
        object_mask,
        support_removed=support_removed,
    )

    result: dict[str, Any] = {
        "crop_recommendation": crop_recommendation,
        "position_analysis": position_analysis,
    }
    if captured_frames is not None:
        result["debug_images"] = _build_cropping_roi_debug_images(
            captured_frames,
            cropping_roi_masks,
            consensus_cropping_mask,
            object_mask,
        )
    return result


def analyze_focus_series(
    frame_reports: list[dict[str, Any]],
    *,
    crop_recommendation: dict[str, Any] | None = None,
    autofocus_quality_ratio: float = 0.9,
    distinct_focus_delta: float = 0.5,
    min_focus_plane_weight: float = 0.5,
) -> dict[str, Any]:
    """Summarize regional ISP focus measurements from a focus sweep.

    The Raspberry Pi ISP supplies a focus value for each cell of its focus
    statistics grid.  This report keeps every measurement and adds small
    heuristics that help decide whether a single autofocus result is likely
    sufficient or whether focus stacking deserves consideration.  The
    heuristic is intentionally conservative because the default sweep has
    only a few focus samples.
    """
    measurements: list[dict[str, Any]] = []
    orientation_flag = _focus_orientation_flag(frame_reports)
    focus_geometry = _focus_crop_geometry(
        crop_recommendation,
        orientation_flag=orientation_flag,
    )
    for report in frame_reports:
        raw_metadata = report.get("camera_metadata", {}).get("raw_metadata", {})
        extended_debug = raw_metadata.get("extended_debug", {})
        focus = extended_debug.get("focus")
        if not focus or not focus.get("regions"):
            continue
        measurements.append(
            {
                "index": report.get("index"),
                "capture_index": report.get("capture_index"),
                "turntable_offset": report.get("turntable_offset"),
                "turntable_angle": report.get("turntable_angle"),
                "focus_mode": report.get("focus_mode"),
                "requested_focus": report.get("requested_focus"),
                "actual_lens_position": report.get("actual_lens_position"),
                "focus_fom": report.get("focus_fom"),
                "source": focus.get("source"),
                "coordinate_system": focus.get("coordinate_system"),
                "grid_size": focus.get("grid_size"),
                "extended_debug": {
                    "schema_version": extended_debug.get("schema_version"),
                    "stats_output_enabled": extended_debug.get("stats_output_enabled"),
                    "sources": [
                        {
                            key: source.get(key)
                            for key in (
                                "metadata_key",
                                "blob_size",
                                "parser_supported",
                                "version",
                                "declared_size",
                                "error",
                            )
                            if key in source
                        }
                        for source in extended_debug.get("sources", [])
                    ],
                    "errors": extended_debug.get("errors", []),
                },
                "regions": [
                    _focus_region_report(region, focus_geometry)
                    for region in focus["regions"]
                ],
            }
        )

    positions: list[dict[str, Any]] = []
    for offset in _ordered_focus_offsets(measurements):
        position_measurements = [
            item for item in measurements
            if item.get("turntable_offset") == offset
        ]
        manual = [item for item in position_measurements if item.get("focus_mode") == "manual"]
        autofocus = next(
            (item for item in position_measurements if item.get("focus_mode") == "autofocus"),
            None,
        )
        positions.append(
            _analyze_focus_position(
                offset,
                autofocus,
                manual,
                autofocus_quality_ratio=autofocus_quality_ratio,
                distinct_focus_delta=distinct_focus_delta,
                min_focus_plane_weight=min_focus_plane_weight,
            )
        )

    if not measurements:
        return {
            "status": "unavailable",
            "reason": "No decoded ISP focus statistics were present in the captures.",
            "measurements": [],
            "positions": [],
        }

    stacking_positions = [
        position for position in positions
        if position.get("recommendation") == "stacking_candidate"
    ]
    autofocus_positions = [
        position for position in positions
        if position.get("recommendation") == "autofocus_sufficient"
    ]
    if stacking_positions:
        status = "stacking_candidate"
    elif positions and len(autofocus_positions) == len(positions):
        status = "autofocus_sufficient"
    else:
        status = "more_data_needed"

    return {
        "status": status,
        "heuristic": {
            "autofocus_quality_ratio": autofocus_quality_ratio,
            "distinct_focus_delta": distinct_focus_delta,
            "min_focus_plane_weight": min_focus_plane_weight,
            "sampling": "coarse_focus_sweep",
            "crop_geometry": focus_geometry,
        },
        "measurements": measurements,
        "positions": positions,
    }


def _ordered_focus_offsets(measurements: list[dict[str, Any]]) -> list[float]:
    """Return turntable offsets in their first observed order."""
    offsets: list[float] = []
    for measurement in measurements:
        value = measurement.get("turntable_offset")
        if value is None:
            continue
        value = float(value)
        if value not in offsets:
            offsets.append(value)
    return offsets


def _analyze_focus_position(
    offset: float,
    autofocus: dict[str, Any] | None,
    manual: list[dict[str, Any]],
    *,
    autofocus_quality_ratio: float,
    distinct_focus_delta: float,
    min_focus_plane_weight: float,
) -> dict[str, Any]:
    """Analyze one turntable position while preserving the regional values."""
    result: dict[str, Any] = {
        "turntable_offset": offset,
        "autofocus": autofocus,
        "manual": manual,
        "manual_sample_count": len(manual),
        "best_focus_by_region": [],
        "best_focus_value_by_region": [],
        "focus_span": None,
        "autofocus_coverage": None,
        "focus_weight_by_setting": {},
        "effective_region_weight": 0.0,
        "recommendation": "insufficient_data",
    }
    if not manual:
        return result

    region_values: dict[int, list[tuple[float, float, float]]] = {}
    for measurement in manual:
        requested_focus = measurement.get("requested_focus")
        if requested_focus is None:
            continue
        for region in measurement.get("regions", []):
            focus_value = region.get("focus_value")
            index = region.get("index")
            if focus_value is None or index is None:
                continue
            region_weight = max(0.0, float(region.get("crop_overlap_weight", 1.0)))
            if region_weight <= 0:
                continue
            region_values.setdefault(int(index), []).append(
                (float(requested_focus), float(focus_value), region_weight)
            )

    winners: list[dict[str, Any]] = []
    winner_focuses: list[float] = []
    for index in sorted(region_values):
        samples = region_values[index]
        best_focus, best_value, region_weight = max(samples, key=lambda item: item[1])
        winners.append(
            {
                "index": index,
                "best_focus": best_focus,
                "focus_value": best_value,
                "crop_overlap_weight": round(region_weight, 6),
                "samples": [
                    {
                        "requested_focus": focus,
                        "focus_value": value,
                        "crop_overlap_weight": round(weight, 6),
                    }
                    for focus, value, weight in samples
                ],
            }
        )
        winner_focuses.append(best_focus)

    result["best_focus_by_region"] = winners
    result["best_focus_value_by_region"] = winner_focuses
    if winner_focuses:
        result["focus_span"] = {
            "min": min(winner_focuses),
            "max": max(winner_focuses),
            "range": max(winner_focuses) - min(winner_focuses),
        }
        focus_weight_by_setting: dict[str, float] = {}
        for winner in winners:
            key = _focus_setting_key(winner["best_focus"])
            focus_weight_by_setting[key] = (
                focus_weight_by_setting.get(key, 0.0)
                + winner["crop_overlap_weight"]
            )
        total_weight = sum(focus_weight_by_setting.values())
        result["focus_weight_by_setting"] = {
            key: round(value / total_weight, 6) if total_weight else 0.0
            for key, value in focus_weight_by_setting.items()
        }
        result["effective_region_weight"] = round(total_weight, 6)

    if autofocus is not None and winners:
        autofocus_values = {
            int(region["index"]): float(region["focus_value"])
            for region in autofocus.get("regions", [])
            if region.get("index") is not None and region.get("focus_value") is not None
        }
        covered = 0
        compared = 0
        for winner in winners:
            autofocus_value = autofocus_values.get(winner["index"])
            if autofocus_value is None:
                continue
            compared += 1
            if winner["focus_value"] <= 0 or autofocus_value >= winner["focus_value"] * autofocus_quality_ratio:
                covered += 1
        result["autofocus_coverage"] = covered / compared if compared else None

    weighted_focuses = [
        float(key)
        for key, weight in result["focus_weight_by_setting"].items()
        if weight >= min_focus_plane_weight
    ]
    distinct_focuses = _distinct_values(weighted_focuses, distinct_focus_delta)
    if len(distinct_focuses) > 1 and result["focus_span"]["range"] >= distinct_focus_delta:
        result["recommendation"] = "stacking_candidate"
    elif result["autofocus_coverage"] is not None and result["autofocus_coverage"] >= 0.8:
        result["recommendation"] = "autofocus_sufficient"
    else:
        result["recommendation"] = "more_data_needed"
    return result


def _distinct_values(values: list[float], delta: float) -> list[float]:
    """Group nearby focus settings so sensor noise does not create planes."""
    distinct: list[float] = []
    for value in sorted(values):
        if not distinct or value - distinct[-1] >= delta:
            distinct.append(value)
    return distinct


def _focus_setting_key(value: float) -> str:
    """Use stable JSON keys for focus settings while retaining numeric values."""
    return f"{value:g}"


def _focus_orientation_flag(frame_reports: list[dict[str, Any]]) -> int:
    """Read the orientation used by the oriented analysis frame."""
    for report in frame_reports:
        settings = report.get("camera_metadata", {}).get("camera_settings", {})
        value = settings.get("orientation_flag")
        if value is not None:
            return int(value)
    return 1


def _focus_crop_geometry(
    crop_recommendation: dict[str, Any] | None,
    *,
    orientation_flag: int,
) -> dict[str, Any]:
    """Describe the crop in the sensor and oriented coordinate systems."""
    crop_rect = (crop_recommendation or {}).get("rect_normalized")
    if not crop_rect:
        return {
            "applied": False,
            "orientation_flag": orientation_flag,
        }
    return {
        "applied": True,
        "orientation_flag": orientation_flag,
        "coordinate_system": "oriented_full_analysis_frame",
        "rect_normalized": [round(float(value), 6) for value in crop_rect],
    }


def _focus_region_report(
    region: dict[str, Any],
    focus_geometry: dict[str, Any],
) -> dict[str, Any]:
    """Add oriented coordinates and crop overlap to one focus cell."""
    sensor_rect = region.get("rect_normalized")
    report = {
        "index": region.get("index"),
        "row": region.get("row"),
        "column": region.get("column"),
        "rect_normalized": sensor_rect,
        "focus_value": region.get("focus_value"),
        "focus_value_raw": region.get("focus_value_raw"),
        "contrast_value_num": region.get("contrast_value_num"),
        "crop_overlap_weight": 1.0,
    }
    if not sensor_rect:
        return report

    oriented_rect = _transform_normalized_rect(
        sensor_rect,
        int(focus_geometry.get("orientation_flag", 1)),
    )
    report["rect_oriented_normalized"] = oriented_rect
    if focus_geometry.get("applied"):
        crop_rect = focus_geometry["rect_normalized"]
        report["crop_overlap_weight"] = round(
            _rectangle_overlap_fraction(oriented_rect, crop_rect),
            6,
        )
    return report


def _transform_normalized_rect(rect: list[float], orientation_flag: int) -> list[float]:
    """Transform a sensor normalized rectangle into oriented coordinates."""
    x, y, width, height = [float(value) for value in rect]
    transformations = {
        1: (x, y, width, height),
        2: (1 - x - width, y, width, height),
        3: (1 - x - width, 1 - y - height, width, height),
        4: (x, 1 - y - height, width, height),
        5: (y, x, height, width),
        6: (1 - y - height, x, height, width),
        7: (1 - y - height, 1 - x - width, height, width),
        8: (y, 1 - x - width, height, width),
    }
    return [round(value, 6) for value in transformations.get(orientation_flag, transformations[1])]


def _rectangle_overlap_fraction(first: list[float], second: list[float]) -> float:
    """Return the fraction of the first rectangle covered by the second."""
    first_x, first_y, first_width, first_height = first
    second_x, second_y, second_width, second_height = second
    overlap_width = max(
        0.0,
        min(first_x + first_width, second_x + second_width)
        - max(first_x, second_x),
    )
    overlap_height = max(
        0.0,
        min(first_y + first_height, second_y + second_height)
        - max(first_y, second_y),
    )
    first_area = first_width * first_height
    return overlap_width * overlap_height / first_area if first_area else 0.0


def prepare_analysis_frame(
    photo: PhotoData,
    position: PolarPoint3D,
    index: int,
    max_dimension: int = 640,
) -> AnalysisFrame:
    """Create a small analysis frame without retaining the raw array."""
    image = photo.data
    if not isinstance(image, np.ndarray) or image.ndim != 3 or image.shape[2] != 3:
        raise ValueError("Analysis frames require an RGB NumPy array.")
    if max_dimension < 1:
        raise ValueError("max_dimension must be positive.")

    orientation_flag = photo.camera_metadata.camera_settings.orientation_flag or 1
    image = apply_orientation(image, orientation_flag)

    height, width = image.shape[:2]
    scale = min(1.0, max_dimension / max(height, width))
    if scale == 1.0:
        analysis_image = image.copy()
    else:
        size = (max(1, round(width * scale)), max(1, round(height * scale)))
        analysis_image = np.asarray(
            Image.fromarray(image).resize(size, Image.Resampling.BILINEAR)
        )

    return AnalysisFrame(
        index=index,
        position=position,
        camera_metadata=photo.camera_metadata,
        analysis_image=analysis_image,
    )


def crop_centered_image(
    image: np.ndarray,
    crop_width: float = 0.0,
    crop_height: float = 0.0,
) -> np.ndarray:
    """Apply the configured centered crop to an analysis image.

    Crop values describe the percentage removed from the complete image size.
    The same crop is removed from both opposing sides.
    """
    if image.ndim < 2:
        raise ValueError("An analysis image must have at least two dimensions.")
    if not 0 <= crop_width <= 100 or not 0 <= crop_height <= 100:
        raise ValueError("Crop percentages must be between 0 and 100.")

    height, width = image.shape[:2]
    target_width = max(1, round(width * (1 - crop_width / 100)))
    target_height = max(1, round(height * (1 - crop_height / 100)))
    x_start = max(0, (width - target_width) // 2)
    y_start = max(0, (height - target_height) // 2)
    return image[
        y_start:y_start + target_height,
        x_start:x_start + target_width,
    ].copy()


def crop_normalized_image(
    image: np.ndarray,
    rect_normalized: list[float] | tuple[float, float, float, float],
) -> np.ndarray:
    """Crop an oriented image using a normalized x, y, width, height rectangle."""
    if image.ndim < 2:
        raise ValueError("An analysis image must have at least two dimensions.")
    if len(rect_normalized) != 4:
        raise ValueError("A normalized crop must contain x, y, width, and height.")

    x, y, width, height = (float(value) for value in rect_normalized)
    if (
        x < 0
        or y < 0
        or width <= 0
        or height <= 0
        or x + width > 1
        or y + height > 1
    ):
        raise ValueError("A normalized crop must be contained within the image.")

    image_height, image_width = image.shape[:2]
    x_start = max(0, min(image_width - 1, round(x * image_width)))
    y_start = max(0, min(image_height - 1, round(y * image_height)))
    x_end = max(x_start + 1, min(image_width, round((x + width) * image_width)))
    y_end = max(y_start + 1, min(image_height, round((y + height) * image_height)))
    return image[y_start:y_end, x_start:x_end].copy()


def prepare_sift_image(
    frame: AnalysisFrame,
    *,
    crop_width: float = 0.0,
    crop_height: float = 0.0,
) -> np.ndarray:
    """Prepare one analysis frame for the SIFT comparison."""
    return crop_centered_image(
        frame.analysis_image,
        crop_width=crop_width,
        crop_height=crop_height,
    )


def analyze_sift_pair(
    first_image: np.ndarray,
    second_image: np.ndarray,
    *,
    ratio_threshold: float = 0.75,
    ransac_reprojection_threshold: float = 3.0,
    grid_rows: int = 4,
    grid_columns: int = 4,
    quality_thresholds: SiftQualityThresholds | None = None,
) -> dict[str, Any]:
    """Measure SIFT features, mutual matches, and geometric outliers.

    Features are matched in both directions with Lowe's ratio test. A match is
    counted as matchable only when it survives both directions. RANSAC uses a
    fundamental matrix because the turntable changes the object's view and a
    single homography would incorrectly reject valid non-planar matches.
    """
    if not 0 < ratio_threshold < 1:
        raise ValueError("ratio_threshold must be between 0 and 1.")
    if ransac_reprojection_threshold <= 0:
        raise ValueError("ransac_reprojection_threshold must be positive.")
    if grid_rows <= 0 or grid_columns <= 0:
        raise ValueError("grid_rows and grid_columns must be positive.")
    quality_thresholds = quality_thresholds or SiftQualityThresholds()

    import cv2

    first_gray = _to_grayscale(first_image, cv2)
    second_gray = _to_grayscale(second_image, cv2)
    sift = cv2.SIFT_create()
    first_keypoints, first_descriptors = sift.detectAndCompute(first_gray, None)
    second_keypoints, second_descriptors = sift.detectAndCompute(second_gray, None)

    first_count = len(first_keypoints)
    second_count = len(second_keypoints)
    result: dict[str, Any] = {
        "feature_count": {"first": first_count, "second": second_count},
        "tentative_match_count": 0,
        "mutual_match_count": 0,
        "matchable_feature_count": {"first": 0, "second": 0},
        "matchable_feature_ratio": {"first": 0.0, "second": 0.0},
        "unmatched_feature_count": {
            "first": first_count,
            "second": second_count,
        },
        "geometric_inlier_count": None,
        "geometric_outlier_count": None,
        "geometric_inlier_ratio": None,
        "spatial_distribution": {
            "grid_size": {
                "rows": grid_rows,
                "columns": grid_columns,
            },
            "first": {
                "feature_counts": _grid_counts(
                    first_keypoints,
                    first_gray.shape,
                    grid_rows,
                    grid_columns,
                ),
                "matchable_feature_counts": _empty_grid(grid_rows, grid_columns),
                "geometric_inlier_counts": None,
            },
            "second": {
                "feature_counts": _grid_counts(
                    second_keypoints,
                    second_gray.shape,
                    grid_rows,
                    grid_columns,
                ),
                "matchable_feature_counts": _empty_grid(grid_rows, grid_columns),
                "geometric_inlier_counts": None,
            },
        },
    }
    if first_descriptors is None or second_descriptors is None:
        return _add_sift_quality(result, quality_thresholds)

    matcher = cv2.BFMatcher(cv2.NORM_L2)
    forward_matches = _ratio_matches(
        matcher.knnMatch(first_descriptors, second_descriptors, k=2),
        ratio_threshold,
    )
    backward_matches = _ratio_matches(
        matcher.knnMatch(second_descriptors, first_descriptors, k=2),
        ratio_threshold,
    )
    backward_pairs = {
        (match.trainIdx, match.queryIdx)
        for match in backward_matches
    }
    mutual_matches = [
        match
        for match in forward_matches
        if (match.queryIdx, match.trainIdx) in backward_pairs
    ]

    first_matchable = len({match.queryIdx for match in mutual_matches})
    second_matchable = len({match.trainIdx for match in mutual_matches})
    result.update(
        {
            "tentative_match_count": len(forward_matches),
            "mutual_match_count": len(mutual_matches),
            "matchable_feature_count": {
                "first": first_matchable,
                "second": second_matchable,
            },
            "matchable_feature_ratio": {
                "first": first_matchable / first_count if first_count else 0.0,
                "second": second_matchable / second_count if second_count else 0.0,
            },
            "unmatched_feature_count": {
                "first": first_count - first_matchable,
                "second": second_count - second_matchable,
            },
        }
    )
    result["spatial_distribution"]["first"]["matchable_feature_counts"] = _grid_counts(
        first_keypoints,
        first_gray.shape,
        grid_rows,
        grid_columns,
        indices={match.queryIdx for match in mutual_matches},
    )
    result["spatial_distribution"]["second"]["matchable_feature_counts"] = _grid_counts(
        second_keypoints,
        second_gray.shape,
        grid_rows,
        grid_columns,
        indices={match.trainIdx for match in mutual_matches},
    )

    if len(mutual_matches) < 8:
        return _add_sift_quality(result, quality_thresholds)

    first_points = np.float32([
        first_keypoints[match.queryIdx].pt for match in mutual_matches
    ])
    second_points = np.float32([
        second_keypoints[match.trainIdx].pt for match in mutual_matches
    ])
    try:
        _, inlier_mask = cv2.findFundamentalMat(
            first_points,
            second_points,
            cv2.FM_RANSAC,
            ransac_reprojection_threshold,
            0.99,
        )
    except cv2.error:
        return _add_sift_quality(result, quality_thresholds)

    if inlier_mask is None:
        return _add_sift_quality(result, quality_thresholds)
    inlier_count = int(np.count_nonzero(inlier_mask))
    inlier_mask = np.asarray(inlier_mask).reshape(-1).astype(bool)
    result.update(
        {
            "geometric_inlier_count": inlier_count,
            "geometric_outlier_count": len(mutual_matches) - inlier_count,
            "geometric_inlier_ratio": inlier_count / len(mutual_matches),
        }
    )
    result["spatial_distribution"]["first"]["geometric_inlier_counts"] = _grid_counts(
        first_keypoints,
        first_gray.shape,
        grid_rows,
        grid_columns,
        indices={
            match.queryIdx
            for match, is_inlier in zip(mutual_matches, inlier_mask)
            if is_inlier
        },
    )
    result["spatial_distribution"]["second"]["geometric_inlier_counts"] = _grid_counts(
        second_keypoints,
        second_gray.shape,
        grid_rows,
        grid_columns,
        indices={
            match.trainIdx
            for match, is_inlier in zip(mutual_matches, inlier_mask)
            if is_inlier
        },
    )
    return _add_sift_quality(result, quality_thresholds)


def _empty_grid(rows: int, columns: int) -> list[list[int]]:
    """Create a zero-filled feature count grid."""
    return [[0 for _ in range(columns)] for _ in range(rows)]


def _grid_counts(
    keypoints: list[Any],
    image_shape: tuple[int, ...],
    rows: int,
    columns: int,
    *,
    indices: set[int] | None = None,
) -> list[list[int]]:
    """Count selected keypoints in a row-major grid over one image."""
    height, width = image_shape[:2]
    counts = _empty_grid(rows, columns)
    if height <= 0 or width <= 0:
        return counts

    selected_indices = range(len(keypoints)) if indices is None else indices
    for index in selected_indices:
        keypoint = keypoints[index]
        column = max(0, min(columns - 1, int(keypoint.pt[0] * columns / width)))
        row = max(0, min(rows - 1, int(keypoint.pt[1] * rows / height)))
        counts[row][column] += 1
    return counts


def _add_sift_quality(
    result: dict[str, Any],
    thresholds: SiftQualityThresholds,
) -> dict[str, Any]:
    """Add qualitative SIFT statuses and warnings to a raw measurement report."""
    distribution = result["spatial_distribution"]
    grid_metrics = {
        image_name: distribution[image_name]
        for image_name in ("first", "second")
    }
    feature_coverage = {
        image_name: _grid_coverage(
            metrics["feature_counts"],
            thresholds.min_feature_per_cell,
        )
        for image_name, metrics in grid_metrics.items()
    }
    matchable_coverage = {
        image_name: _grid_coverage(
            metrics["matchable_feature_counts"],
            thresholds.min_matchable_feature_per_cell,
        )
        for image_name, metrics in grid_metrics.items()
    }
    low_feature_cells = {
        image_name: _grid_cells_below(
            metrics["feature_counts"],
            thresholds.min_feature_per_cell,
        )
        for image_name, metrics in grid_metrics.items()
    }
    low_matchable_cells = {
        image_name: _grid_cells_below(
            metrics["matchable_feature_counts"],
            thresholds.min_matchable_feature_per_cell,
        )
        for image_name, metrics in grid_metrics.items()
    }
    feature_coverage_status = {
        image_name: _lower_status(
            feature_coverage[image_name],
            thresholds.min_feature_coverage,
            thresholds.critical_feature_coverage,
        )
        for image_name in ("first", "second")
    }
    matchable_coverage_status = {
        image_name: _lower_status(
            matchable_coverage[image_name],
            thresholds.min_matchable_coverage,
            thresholds.critical_matchable_coverage,
        )
        for image_name in ("first", "second")
    }

    feature_counts = result["feature_count"]
    matchable_ratios = result["matchable_feature_ratio"]
    feature_balance = _balance_ratio(feature_counts["first"], feature_counts["second"])
    matchable_balance = _balance_ratio(
        matchable_ratios["first"],
        matchable_ratios["second"],
    )
    feature_quality = _worst_status(
        [
            _lower_status(
                feature_counts[image_name],
                thresholds.min_feature_count,
                thresholds.critical_feature_count,
            )
            for image_name in ("first", "second")
        ]
        + [_balance_status(feature_balance, thresholds)]
    )
    spatial_quality = _worst_status(
        list(feature_coverage_status.values())
        + list(matchable_coverage_status.values())
    )

    matching_statuses = [
        _lower_status(
            result["mutual_match_count"],
            thresholds.min_mutual_match_count,
            thresholds.critical_mutual_match_count,
        ),
        *(
            _lower_status(
                matchable_ratios[image_name],
                thresholds.min_matchable_feature_ratio,
                thresholds.critical_matchable_feature_ratio,
            )
            for image_name in ("first", "second")
        ),
        _balance_status(matchable_balance, thresholds),
    ]
    inlier_ratio = result["geometric_inlier_ratio"]
    matching_statuses.append(
        "warning"
        if inlier_ratio is None and result["mutual_match_count"] >= 8
        else _lower_status(
            inlier_ratio,
            thresholds.min_geometric_inlier_ratio,
            thresholds.critical_geometric_inlier_ratio,
        )
    )
    matching_quality = _worst_status(matching_statuses)

    warnings: list[dict[str, Any]] = []
    for image_name in ("first", "second"):
        if feature_counts[image_name] < thresholds.min_feature_count:
            status = _lower_status(
                feature_counts[image_name],
                thresholds.min_feature_count,
                thresholds.critical_feature_count,
            )
            warnings.append(
                _sift_warning(
                    "LOW_FEATURE_COUNT",
                    status,
                    f"{image_name} contains too few SIFT features.",
                    images=[image_name],
                )
            )
        if matchable_ratios[image_name] < thresholds.min_matchable_feature_ratio:
            status = _lower_status(
                matchable_ratios[image_name],
                thresholds.min_matchable_feature_ratio,
                thresholds.critical_matchable_feature_ratio,
            )
            warnings.append(
                _sift_warning(
                    "LOW_MATCHABLE_FEATURE_RATIO",
                    status,
                    f"{image_name} has too few features that match the other image.",
                    images=[image_name],
                )
            )
        if (
            feature_coverage_status[image_name] != "good"
            and low_feature_cells[image_name]
        ):
            coverage_status = feature_coverage_status[image_name]
            warnings.append(
                _sift_warning(
                    "LOW_FEATURE_COVERAGE",
                    coverage_status,
                    f"{image_name} has weak spatial feature coverage.",
                    images=[image_name],
                    cells=low_feature_cells[image_name],
                )
            )
        if (
            matchable_coverage_status[image_name] != "good"
            and low_matchable_cells[image_name]
        ):
            coverage_status = matchable_coverage_status[image_name]
            warnings.append(
                _sift_warning(
                    "LOW_MATCHABLE_COVERAGE",
                    coverage_status,
                    f"{image_name} has weak spatial coverage of matchable features.",
                    images=[image_name],
                    cells=low_matchable_cells[image_name],
                )
            )

    if result["mutual_match_count"] < thresholds.min_mutual_match_count:
        status = _lower_status(
            result["mutual_match_count"],
            thresholds.min_mutual_match_count,
            thresholds.critical_mutual_match_count,
        )
        warnings.append(
            _sift_warning(
                "LOW_MUTUAL_MATCH_COUNT",
                status,
                "Too few mutually consistent feature matches were found.",
            )
        )
    if feature_balance < thresholds.min_balance_ratio:
        warnings.append(
            _sift_warning(
                "UNBALANCED_FEATURE_COUNTS",
                _balance_status(feature_balance, thresholds),
                "The two images contain substantially different numbers of features.",
            )
        )
    if matchable_balance < thresholds.min_balance_ratio:
        warnings.append(
            _sift_warning(
                "UNBALANCED_MATCHABLE_RATIOS",
                _balance_status(matchable_balance, thresholds),
                "The matchable feature ratios differ substantially between images.",
            )
        )
    if inlier_ratio is None:
        if result["mutual_match_count"] >= 8:
            warnings.append(
                _sift_warning(
                    "GEOMETRY_NOT_AVAILABLE",
                    "warning",
                    "Geometric consistency could not be calculated.",
                )
            )
    elif inlier_ratio < thresholds.min_geometric_inlier_ratio:
        warnings.append(
            _sift_warning(
                "LOW_GEOMETRIC_INLIER_RATIO",
                _lower_status(
                    inlier_ratio,
                    thresholds.min_geometric_inlier_ratio,
                    thresholds.critical_geometric_inlier_ratio,
                ),
                "Too many mutual matches are geometrically inconsistent.",
            )
        )

    quality = {
        "overall_status": _worst_status(
            [feature_quality, spatial_quality, matching_quality]
        ),
        "feature_quality": feature_quality,
        "spatial_coverage": spatial_quality,
        "matching_quality": matching_quality,
        "metrics": {
            "feature_coverage": feature_coverage,
            "feature_coverage_status": feature_coverage_status,
            "matchable_coverage": matchable_coverage,
            "matchable_coverage_status": matchable_coverage_status,
            "low_feature_cell_count": {
                image_name: len(cells)
                for image_name, cells in low_feature_cells.items()
            },
            "low_matchable_cell_count": {
                image_name: len(cells)
                for image_name, cells in low_matchable_cells.items()
            },
            "feature_count_balance": feature_balance,
            "matchable_ratio_balance": matchable_balance,
        },
        "thresholds": asdict(thresholds),
        "warnings": warnings,
    }
    result["quality"] = quality
    return result


def _grid_coverage(grid: list[list[int]], minimum: int) -> float:
    """Return the fraction of grid cells meeting a minimum count."""
    total_cells = sum(len(row) for row in grid)
    if total_cells == 0:
        return 0.0
    occupied_cells = sum(
        1
        for row in grid
        for count in row
        if count >= minimum
    )
    return occupied_cells / total_cells


def _grid_cells_below(grid: list[list[int]], minimum: int) -> list[list[int]]:
    """Return row and column indices for grid cells below a minimum count."""
    return [
        [row_index, column_index]
        for row_index, row in enumerate(grid)
        for column_index, count in enumerate(row)
        if count < minimum
    ]


def _balance_ratio(first: float, second: float) -> float:
    """Return the smaller value divided by the larger value."""
    maximum = max(first, second)
    return min(first, second) / maximum if maximum else 1.0


def _lower_status(
    value: float | int | None,
    good_threshold: float,
    critical_threshold: float,
) -> str:
    """Classify a metric where larger values are better."""
    if value is None or value < critical_threshold:
        return "poor"
    if value < good_threshold:
        return "warning"
    return "good"


def _balance_status(value: float, thresholds: SiftQualityThresholds) -> str:
    """Classify the balance between the two images."""
    return _lower_status(
        value,
        thresholds.min_balance_ratio,
        thresholds.critical_balance_ratio,
    )


def _worst_status(statuses: list[str]) -> str:
    """Return the worst quality status from a list."""
    ranking = {"good": 0, "warning": 1, "poor": 2}
    return max(statuses, key=lambda status: ranking[status]) if statuses else "poor"


def _sift_warning(
    code: str,
    status: str,
    message: str,
    *,
    images: list[str] | None = None,
    cells: list[list[int]] | None = None,
) -> dict[str, Any]:
    """Build one structured SIFT quality warning."""
    return {
        "code": code,
        "severity": "error" if status == "poor" else "warning",
        "message": message,
        "images": images,
        "cells": cells,
    }


def _to_grayscale(image: np.ndarray, cv2: Any) -> np.ndarray:
    """Convert an RGB or grayscale analysis image to an 8-bit grayscale image."""
    if not isinstance(image, np.ndarray) or image.ndim not in (2, 3):
        raise ValueError("SIFT images must be NumPy arrays with two or three dimensions.")
    if image.ndim == 3:
        if image.shape[2] != 3:
            raise ValueError("SIFT RGB images must have three channels.")
        image = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    if image.dtype != np.uint8:
        image = np.clip(image, 0, 255).astype(np.uint8)
    return image


def _ratio_matches(matches: list[list[Any]], ratio_threshold: float) -> list[Any]:
    """Keep descriptor matches that pass Lowe's ratio test."""
    return [
        candidates[0]
        for candidates in matches
        if len(candidates) >= 2
        and candidates[0].distance < ratio_threshold * candidates[1].distance
    ]


def apply_orientation(image: np.ndarray, orientation_flag: int) -> np.ndarray:
    """Apply an EXIF orientation to an RGB array for UI-facing coordinates."""
    transpose = {
        2: Image.Transpose.FLIP_LEFT_RIGHT,
        3: Image.Transpose.ROTATE_180,
        4: Image.Transpose.FLIP_TOP_BOTTOM,
        5: Image.Transpose.TRANSPOSE,
        6: Image.Transpose.ROTATE_270,
        7: Image.Transpose.TRANSVERSE,
        8: Image.Transpose.ROTATE_90,
    }.get(int(orientation_flag or 1))
    if transpose is None:
        return image.copy()
    return np.asarray(Image.fromarray(image).transpose(transpose)).copy()


def _detect_cropping_roi_mask(frames: list[AnalysisFrame]) -> _CroppingRoiMask:
    """Build one cropping ROI mask from multiple turntable views."""
    if len(frames) < 2:
        raise ValueError("Cropping ROI analysis requires at least two frames.")

    images = np.stack([frame.analysis_image for frame in frames]).astype(np.float32)
    if images.ndim != 4 or images.shape[3] != 3:
        raise ValueError("Cropping ROI analysis requires RGB analysis images.")
    if len({tuple(image.shape) for image in images}) != 1:
        raise ValueError("All analysis images must have the same shape.")

    background = np.median(images, axis=0).astype(np.uint8)
    variation = np.ptp(images, axis=0).mean(axis=2)
    threshold = _derive_cropping_roi_threshold(variation)
    return _CroppingRoiMask(
        background=background,
        variation=variation,
        mask=_clean_mask(variation > threshold),
    )


def _encode_analysis_image(image: np.ndarray, quality: int = 82) -> str:
    """Encode one small RGB analysis image as base64 JPEG data."""
    buffer = io.BytesIO()
    Image.fromarray(image).save(buffer, format="JPEG", quality=quality)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _create_cropping_roi_debug_images(detection: _CroppingRoiMask) -> dict[str, str]:
    """Create compact debug images for inspecting a cropping ROI mask."""
    return {
        "background_base64": _encode_analysis_image(detection.background),
        "variation_base64": _encode_gray_image(detection.variation),
        "roi_mask_base64": _encode_gray_image(detection.mask.astype(np.uint8) * 255),
    }


def _calculate_position_imbalance(
    mask: np.ndarray,
    *,
    support_removed: bool = False,
) -> dict[str, Any]:
    """Calculate object distribution imbalance and setup guidance."""
    height, width = mask.shape
    half_height = height // 2
    half_width = width // 2
    top = float(mask[:half_height].mean())
    bottom = float(mask[half_height:].mean())
    left = float(mask[:, :half_width].mean())
    right = float(mask[:, half_width:].mean())
    vertical_imbalance = round(_imbalance(top, bottom), 6)
    horizontal_imbalance = round(_imbalance(left, right), 6)
    vertical_assessment = _imbalance_assessment(
        vertical_imbalance,
        axis="vertical",
        support_removed=support_removed,
    )
    horizontal_assessment = _imbalance_assessment(
        horizontal_imbalance,
        axis="horizontal",
    )
    return {
        "vertical_imbalance": vertical_imbalance,
        "horizontal_imbalance": horizontal_imbalance,
        "vertical_assessment": vertical_assessment,
        "horizontal_assessment": horizontal_assessment,
        "overall_status": (
            "good"
            if vertical_assessment["level"] == "balanced"
            and horizontal_assessment["level"] == "balanced"
            else "warning"
        ),
    }


def _imbalance_assessment(
    value: float,
    *,
    axis: str,
    support_removed: bool = False,
) -> dict[str, str]:
    """Turn an imbalance value into setup guidance for the user."""
    if value < 0.15:
        return {
            "level": "balanced",
            "recommendation": "none",
            "message": f"The object is balanced on the {axis} axis.",
        }
    if value < 0.30:
        recommendation = (
            "consider_centering_object"
            if axis == "vertical" and support_removed
            else "inspect_turntable_holder"
            if axis == "vertical"
            else "consider_centering_object"
        )
        return {
            "level": "moderate",
            "recommendation": recommendation,
            "message": (
                f"The object is moderately imbalanced on the {axis} axis; "
                "check its alignment."
            ),
        }
    recommendation = (
        "center_object"
        if axis == "vertical" and support_removed
        else "check_turntable_holder"
        if axis == "vertical"
        else "center_object"
    )
    return {
        "level": "strong",
        "recommendation": recommendation,
        "message": (
            f"The object is strongly imbalanced on the {axis} axis; "
            + (
                (
                    "the object should be centered better."
                    if support_removed
                    else "the turntable holder may be visible or misaligned."
                )
                if axis == "vertical"
                else "the object should be centered better."
            )
        ),
    }


def _combine_cropping_roi_masks(
    roi_masks: dict[str, _CroppingRoiMask],
) -> np.ndarray:
    """Keep pixels detected by a majority of the cropping ROI series."""
    if not roi_masks:
        raise ValueError("At least one cropping ROI mask is required.")
    masks = np.stack([roi_mask.mask for roi_mask in roi_masks.values()])
    required_votes = masks.shape[0] // 2 + 1
    return masks.sum(axis=0) >= required_votes


def _remove_narrow_cropping_roi_tail(
    mask: np.ndarray,
    width_ratio: float = 0.4,
    min_rows: int = 12,
) -> np.ndarray:
    """Remove a long, narrow bottom tail from a cropping ROI mask."""
    if not 0 < width_ratio < 1:
        raise ValueError("width_ratio must be between 0 and 1.")
    if min_rows < 1:
        raise ValueError("min_rows must be positive.")

    row_widths = np.zeros(mask.shape[0], dtype=int)
    for row, values in enumerate(mask):
        columns = np.flatnonzero(values)
        if len(columns):
            row_widths[row] = int(columns[-1] - columns[0] + 1)

    nonzero_widths = row_widths[row_widths > 0]
    if not len(nonzero_widths):
        return mask

    reference_width = float(np.percentile(nonzero_widths, 75))
    width_threshold = max(3.0, reference_width * width_ratio)
    end = int(np.flatnonzero(row_widths > 0)[-1])
    start = end
    while start >= 0 and row_widths[start] <= width_threshold:
        start -= 1

    tail_length = end - start
    if tail_length < min_rows:
        return mask

    trimmed = mask.copy()
    trimmed[start + 1:] = False
    return trimmed


def _remove_bottom_cropping_roi_support(
    mask: np.ndarray,
    *,
    min_rows: int = 12,
    min_width_growth: float = 1.4,
) -> np.ndarray:
    """Remove a broad support that starts below the object.

    The temporal ROI contains everything that rotates with the object, including
    a holder attached below it.  A colour test would depend on the particular
    holder, so this heuristic uses only the silhouette.  A holder often starts
    with a broad shoulder below a narrower object, or widens after a narrow
    transition.  Objects without either profile are left unchanged.
    """
    if min_rows < 1:
        raise ValueError("min_rows must be positive.")
    if min_width_growth <= 1:
        raise ValueError("min_width_growth must be greater than 1.")

    row_widths = np.zeros(mask.shape[0], dtype=float)
    for row, values in enumerate(mask):
        columns = np.flatnonzero(values)
        if len(columns):
            row_widths[row] = columns[-1] - columns[0] + 1

    occupied_rows = np.flatnonzero(row_widths > 0)
    if not len(occupied_rows):
        return mask

    start = int(occupied_rows[0])
    end = int(occupied_rows[-1])
    object_height = end - start + 1
    if object_height < min_rows * 2:
        return mask

    smoothing_radius = max(1, min(8, object_height // 30))
    padded = np.pad(
        row_widths,
        smoothing_radius,
        mode="edge",
    )
    kernel = np.ones(2 * smoothing_radius + 1) / (2 * smoothing_radius + 1)
    smoothed = np.convolve(padded, kernel, mode="valid")

    search_start = start + max(min_rows, int(object_height * 0.55))
    search_end = end - min_rows + 1
    if search_start >= search_end:
        return mask

    # First detect a broad shoulder.  Comparing short windows makes this work
    # for holders whose cone narrows below the shoulder as well as for holders
    # that widen all the way to the bottom.
    window = max(4, min(12, object_height // 20))
    for candidate in range(search_start, search_end):
        before_start = max(start, candidate - window)
        before_width = float(np.median(smoothed[before_start:candidate]))
        after_end = min(end + 1, candidate + 1 + min_rows)
        after_width = float(np.median(smoothed[candidate + 1:after_end]))
        if (
            before_width > 0
            and after_width / before_width >= min_width_growth
        ):
            trimmed = mask.copy()
            trimmed[candidate + 1:] = False
            return trimmed

    # If there is no abrupt shoulder, look for a waist in the lower part of the
    # silhouette.  The last rows are sampled separately so a wide object body
    # alone does not trigger removal.
    bottom_sample_start = max(search_start + 1, end - max(min_rows, object_height // 8) + 1)
    bottom_width = float(np.median(smoothed[bottom_sample_start:end + 1]))
    if bottom_width <= 0:
        return mask

    candidate = min(
        range(search_start, search_end),
        key=lambda row: smoothed[row],
    )
    waist_width = float(smoothed[candidate])
    if waist_width <= 0 or bottom_width / waist_width < min_width_growth:
        return mask

    trimmed = mask.copy()
    trimmed[candidate + 1:] = False
    return trimmed


def _crop_recommendation(mask: np.ndarray, margin_ratio: float = 0.0) -> dict[str, object]:
    """Return a centered crop that contains the outer mask extent."""
    if not 0 <= margin_ratio <= 0.25:
        raise ValueError("margin_ratio must be between 0 and 0.25.")
    height, width = mask.shape
    result: dict[str, object] = {
        "coordinate_system": "oriented_full_analysis_frame",
        "frame_size": {
            "width": width,
            "height": height,
        },
        "bbox": None,
        "bbox_normalized": None,
        "rect": None,
        "rect_normalized": None,
        "crop_width": None,
        "crop_height": None,
    }
    y_coordinates, x_coordinates = np.where(mask)
    if not len(x_coordinates):
        return result

    x_min, x_max = int(x_coordinates.min()), int(x_coordinates.max()) + 1
    y_min, y_max = int(y_coordinates.min()), int(y_coordinates.max()) + 1
    bbox = [x_min, y_min, x_max - x_min, y_max - y_min]
    margin_x = width * margin_ratio
    margin_y = height * margin_ratio
    x_min = max(0.0, x_min - margin_x)
    y_min = max(0.0, y_min - margin_y)
    x_max = min(float(width), x_max + margin_x)
    y_max = min(float(height), y_max + margin_y)

    center_x, center_y = width / 2, height / 2
    half_width = max(center_x - x_min, x_max - center_x)
    half_height = max(center_y - y_min, y_max - center_y)
    rect = [
        round(center_x - half_width),
        round(center_y - half_height),
        round(2 * half_width),
        round(2 * half_height),
    ]
    result.update(
        {
            "bbox": bbox,
            "bbox_normalized": _normalize_rect(bbox, width, height),
            "rect": rect,
            "rect_normalized": _normalize_rect(rect, width, height),
            "crop_width": round(100 * (1 - rect[2] / width), 2),
            "crop_height": round(100 * (1 - rect[3] / height), 2),
        }
    )
    return result


def _normalize_rect(
    rect: list[int],
    width: int,
    height: int,
) -> list[float]:
    """Convert an x, y, width, height rectangle to relative coordinates."""
    x, y, rect_width, rect_height = rect
    return [
        round(x / width, 6),
        round(y / height, 6),
        round(rect_width / width, 6),
        round(rect_height / height, 6),
    ]


def _imbalance(first: float, second: float) -> float:
    """Return the relative difference between two area weights."""
    total = first + second
    return abs(first - second) / total if total else 0.0


def _derive_cropping_roi_threshold(variation: np.ndarray) -> float:
    """Derive a noise-aware threshold for the cropping ROI variation."""
    border = np.concatenate(
        (
            variation[0, :],
            variation[-1, :],
            variation[1:-1, 0],
            variation[1:-1, -1],
        )
    )
    median = float(np.median(border))
    mad = float(np.median(np.abs(border - median)))
    return max(12.0, median + 4.0 * max(mad, 1.0))


def _encode_gray_image(image: np.ndarray, quality: int = 82) -> str:
    """Encode a grayscale debug image after normalizing its display range."""
    values = image.astype(np.float32)
    minimum = float(values.min())
    maximum = float(values.max())
    if maximum <= minimum:
        normalized = np.zeros(values.shape, dtype=np.uint8)
    else:
        normalized = ((values - minimum) * 255 / (maximum - minimum)).astype(np.uint8)
    buffer = io.BytesIO()
    Image.fromarray(normalized, mode="L").save(buffer, format="JPEG", quality=quality)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _clean_mask(mask: np.ndarray) -> np.ndarray:
    """Remove isolated noise with a small opening and closing operation."""
    opened = _dilate(_erode(mask))
    return _erode(_dilate(opened))


def _erode(mask: np.ndarray) -> np.ndarray:
    """Keep pixels whose complete 3x3 neighborhood is set."""
    padded = np.pad(mask, 1, mode="constant")
    neighbors = sum(
        padded[row:row + mask.shape[0], column:column + mask.shape[1]]
        for row in range(3)
        for column in range(3)
    )
    return neighbors == 9


def _dilate(mask: np.ndarray) -> np.ndarray:
    """Keep pixels with at least one set pixel in their 3x3 neighborhood."""
    padded = np.pad(mask, 1, mode="constant")
    neighbors = sum(
        padded[row:row + mask.shape[0], column:column + mask.shape[1]]
        for row in range(3)
        for column in range(3)
    )
    return neighbors > 0


def _build_cropping_roi_debug_images(
    captured_frames: list[tuple[AnalysisFrame, dict[str, Any]]],
    cropping_roi_masks: dict[str, _CroppingRoiMask],
    consensus_mask: np.ndarray,
    object_mask: np.ndarray,
) -> dict[str, Any]:
    """Build optional images for inspecting captured frames and cropping ROI masks."""
    return {
        "captured_frames": [
            {
                "index": report["index"],
                "capture_index": report["capture_index"],
                "focus_mode": report["focus_mode"],
                "requested_focus": report["requested_focus"],
                "camera_metadata": report["camera_metadata"],
                "image_base64": _encode_analysis_image(frame.analysis_image),
            }
            for frame, report in captured_frames
        ],
        "cropping_roi_masks": {
            focus: _create_cropping_roi_debug_images(roi_mask)
            for focus, roi_mask in cropping_roi_masks.items()
        },
        "consensus_mask_base64": _encode_gray_image(consensus_mask.astype("uint8") * 255),
        "object_mask_base64": _encode_gray_image(object_mask.astype("uint8") * 255),
    }
