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
from openscan_firmware.utils.photos.focus_options import build_stacking_options


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

    min_analysis_features: int = 150
    min_geometry_matches: int = 50
    bad_feature_density: float = 300
    good_feature_density: float = 1500
    great_feature_density: float = 5000
    bad_geometric_feature_ratio: float = 0.15
    good_geometric_feature_ratio: float = 0.30
    great_geometric_feature_ratio: float = 0.55
    bad_inlier_ratio: float = 0.50
    good_inlier_ratio: float = 0.75
    great_inlier_ratio: float = 0.95
    min_feature_per_cell: int = 5
    min_inliers_per_cell: int = 2


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
) -> dict[str, Any] | None:
    """Return crop-weighted regional focus profiles without quality policy.

    Each region is normalized against its best positive measured ISP focus
    value, including AF. Missing/invalid values and all-zero profiles are null.
    Values indicate relative measurements, not absolute image sharpness.
    """
    geometry = _focus_crop_geometry(
        crop_recommendation, orientation_flag=_focus_orientation_flag(frame_reports),
    )
    offsets = list(dict.fromkeys(
        float(report["turntable_offset"])
        for report in frame_reports if report.get("turntable_offset") is not None
    ))
    focus_keys = list(dict.fromkeys(
        _focus_setting_key(float(report["requested_focus"]))
        for report in frame_reports
        if report.get("focus_mode") == "manual" and report.get("requested_focus") is not None
    ))
    positions = []
    for offset in offsets:
        regions: dict[str, dict[str, Any]] = {}
        autofocus_position = None
        for report in frame_reports:
            if report.get("turntable_offset") != offset:
                continue
            mode = report.get("focus_mode")
            if mode not in ("autofocus", "manual"):
                continue
            if mode == "autofocus":
                measured_position = report.get("actual_lens_position")
                if measured_position is not None and np.isfinite(float(measured_position)):
                    autofocus_position = float(measured_position)
            requested = report.get("requested_focus")
            if mode == "manual" and requested is None:
                continue
            focus = report.get("camera_metadata", {}).get("raw_metadata", {}).get(
                "extended_debug", {}
            ).get("focus") or {}
            for cell in focus.get("regions", []):
                if cell.get("index") is None:
                    continue
                cell = _focus_region_report(cell, geometry)
                weight = cell["crop_overlap_weight"]
                if weight <= 0:
                    continue
                region = regions.setdefault(str(cell["index"]), {
                    "weight": weight,
                    "autofocus": None,
                    "manual": dict.fromkeys(focus_keys),
                })
                value = cell.get("focus_value")
                value = float(value) if value is not None else None
                if value is not None and (not np.isfinite(value) or value < 0):
                    value = None
                if mode == "autofocus":
                    region["autofocus"] = value
                else:
                    region["manual"][_focus_setting_key(float(requested))] = value

        total_weight = sum(region["weight"] for region in regions.values())
        for region in regions.values():
            region["weight"] /= total_weight
            values = [region["autofocus"], *region["manual"].values()]
            best = max((value for value in values if value is not None), default=0.0)

            def relative(value: float | None) -> float | None:
                return value / best if value is not None and best > 0 else None

            region["autofocus"] = relative(region["autofocus"])
            region["manual"] = {key: relative(value) for key, value in region["manual"].items()}
        positions.append({
            "turntable_offset": offset,
            "autofocus_position": autofocus_position,
            "regions": regions,
        })
    if not any(position["regions"] for position in positions):
        return None
    return {"positions": positions, "stacking_options": build_stacking_options(positions)}


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
    max_dimension: int | None = 640,
    crop: tuple[float, float, float, float] | None = None,
) -> AnalysisFrame:
    """Orient and optionally crop before resizing; None preserves native pixels.

    The returned image owns its pixels so the full camera array can be freed.
    """
    image = photo.data
    if not isinstance(image, np.ndarray) or image.ndim != 3 or image.shape[2] != 3:
        raise ValueError("Analysis frames require an RGB NumPy array.")
    if max_dimension is not None and max_dimension < 1:
        raise ValueError("max_dimension must be positive.")

    orientation_flag = photo.camera_metadata.camera_settings.orientation_flag or 1
    image = apply_orientation(image, orientation_flag)
    if crop is not None:
        image = crop_normalized_image(image, crop)

    height, width = image.shape[:2]
    scale = min(1.0, max_dimension / max(height, width)) if max_dimension is not None else 1.0
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

    All detected features contribute to counts and spatial distribution.
    Matching checks up to 1000 strongest, grid-balanced features per image
    against the full opposite descriptor pool, with full-pool reverse checks.
    Per-image matchability counts/ratios refer only to that selected sample.
    Pair counts are deduplicated unions of both image samples. RANSAC uses a
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
    first_sample = _strongest_grid_features(first_keypoints, first_gray.shape, grid_rows, grid_columns)
    second_sample = _strongest_grid_features(second_keypoints, second_gray.shape, grid_rows, grid_columns)
    first_selected, second_selected = set(first_sample), set(second_sample)
    result: dict[str, Any] = {
        "feature_count": {"first": first_count, "second": second_count},
        "feature_density": {
            "first": first_count / (first_gray.shape[0] * first_gray.shape[1] / 1e6),
            "second": second_count / (second_gray.shape[0] * second_gray.shape[1] / 1e6),
        },
        "tentative_match_count": 0,
        "mutual_match_count": 0,
        "matchable_feature_count": {"first": 0, "second": 0},
        "matchable_feature_ratio": {"first": 0.0, "second": 0.0},
        "unmatched_feature_count": {
            "first": len(first_sample),
            "second": len(second_sample),
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
    forward, forward_tentative = _sample_mutual_matches(
        matcher, first_descriptors, second_descriptors, first_sample, ratio_threshold,
    )
    backward, backward_tentative = _sample_mutual_matches(
        matcher, second_descriptors, first_descriptors, second_sample, ratio_threshold,
    )
    mutual_matches = sorted(set(forward) | {(second, first) for first, second in backward})
    first_matchable = len({first for first, _ in mutual_matches} & first_selected)
    second_matchable = len({second for _, second in mutual_matches} & second_selected)
    result.update(
        {
            "tentative_match_count": len(set(forward_tentative) | {(second, first) for first, second in backward_tentative}),
            "mutual_match_count": len(mutual_matches),
            "matchable_feature_count": {
                "first": first_matchable,
                "second": second_matchable,
            },
            "matchable_feature_ratio": {
                "first": first_matchable / len(first_sample) if first_sample else 0.0,
                "second": second_matchable / len(second_sample) if second_sample else 0.0,
            },
            "unmatched_feature_count": {
                "first": len(first_sample) - first_matchable,
                "second": len(second_sample) - second_matchable,
            },
        }
    )
    result["spatial_distribution"]["first"]["matchable_feature_counts"] = _grid_counts(
        first_keypoints,
        first_gray.shape,
        grid_rows,
        grid_columns,
        indices={match[0] for match in mutual_matches} & first_selected,
    )
    result["spatial_distribution"]["second"]["matchable_feature_counts"] = _grid_counts(
        second_keypoints,
        second_gray.shape,
        grid_rows,
        grid_columns,
        indices={match[1] for match in mutual_matches} & second_selected,
    )

    if len(mutual_matches) < 8:
        return _add_sift_quality(result, quality_thresholds)

    first_points = np.float32([
        first_keypoints[match[0]].pt for match in mutual_matches
    ])
    second_points = np.float32([
        second_keypoints[match[1]].pt for match in mutual_matches
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
            match[0]
            for match, is_inlier in zip(mutual_matches, inlier_mask)
            if is_inlier
        } & first_selected,
    )
    result["spatial_distribution"]["second"]["geometric_inlier_counts"] = _grid_counts(
        second_keypoints,
        second_gray.shape,
        grid_rows,
        grid_columns,
        indices={
            match[1]
            for match, is_inlier in zip(mutual_matches, inlier_mask)
            if is_inlier
        } & second_selected,
    )
    return _add_sift_quality(result, quality_thresholds)


def _strongest_grid_features(keypoints, shape, rows, columns, budget=1000):
    """Round-robin strongest responses per cell; redistribute unused slots."""
    height, width = shape[:2]
    cells = [[] for _ in range(rows * columns)]
    for index, keypoint in enumerate(keypoints):
        col = max(0, min(columns - 1, int(keypoint.pt[0] * columns / width)))
        row = max(0, min(rows - 1, int(keypoint.pt[1] * rows / height)))
        cells[row * columns + col].append(index)
    for cell in cells:
        cell.sort(key=lambda index: (-keypoints[index].response, index))
    selected = []
    rank = 0
    while len(selected) < min(budget, len(keypoints)):
        for cell in cells:
            if rank < len(cell):
                selected.append(cell[rank])
                if len(selected) == budget:
                    break
        rank += 1
    return selected


def _sample_mutual_matches(matcher, source, target, selected, ratio_threshold):
    """Sample queries against the full target, then verify against full source."""
    if not selected:
        return [], []
    forward = _ratio_matches(matcher.knnMatch(source[selected], target, k=2), ratio_threshold)
    target_indices = sorted({match.trainIdx for match in forward})
    if not target_indices:
        return [], []
    backward = _ratio_matches(matcher.knnMatch(target[target_indices], source, k=2), ratio_threshold)
    reverse_pairs = {(match.trainIdx, target_indices[match.queryIdx]) for match in backward}
    pairs = [(selected[match.queryIdx], match.trainIdx) for match in forward]
    return [pair for pair in pairs if pair in reverse_pairs], pairs


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
    """Rate core evidence and spatial crop coverage separately."""
    names = ("first", "second")
    counts = result["feature_count"]
    distribution = result["spatial_distribution"]
    enough_features = all(counts[name] >= thresholds.min_analysis_features for name in names)
    enough_matches = result["mutual_match_count"] >= thresholds.min_geometry_matches
    geometry_available = result["geometric_inlier_ratio"] is not None and all(
        distribution[name]["geometric_inlier_counts"] is not None for name in names
    )
    supported = enough_features and enough_matches and geometry_available
    feature_status = {
        name: _quality_band(result["feature_density"][name],
                            thresholds.bad_feature_density,
                            thresholds.good_feature_density,
                            thresholds.great_feature_density)
        for name in names
    }
    feature_quality = _worst_status(list(feature_status.values()))
    if not enough_features:
        feature_quality = "bad"

    geometric_ratios = {
        name: (
            sum(map(sum, distribution[name]["geometric_inlier_counts"])) / min(counts[name], 1000)
            if distribution[name]["geometric_inlier_counts"] is not None and counts[name] else None
        ) for name in names
    }
    matching_quality = (
        _worst_status([
            _quality_band(geometric_ratios[name], thresholds.bad_geometric_feature_ratio,
                          thresholds.good_geometric_feature_ratio, thresholds.great_geometric_feature_ratio)
            for name in names
        ]) if supported else "insufficient_data"
    )
    geometry_quality = (
        _quality_band(result["geometric_inlier_ratio"], thresholds.bad_inlier_ratio,
                      thresholds.good_inlier_ratio, thresholds.great_inlier_ratio)
        if supported else "insufficient_data"
    )
    feature_coverage = {
        name: _grid_coverage(distribution[name]["feature_counts"], thresholds.min_feature_per_cell)
        for name in names
    }
    geometric_coverage = {
        name: (
            _grid_coverage(distribution[name]["geometric_inlier_counts"], thresholds.min_inliers_per_cell)
            if distribution[name]["geometric_inlier_counts"] is not None else None
        ) for name in names
    }
    feature_coverage_status = {
        name: (_quality_band(feature_coverage[name], 0.25, 0.5, 0.75)
               if counts[name] >= thresholds.min_analysis_features else "insufficient_data")
        for name in names
    }
    geometric_coverage_status = {
        name: (_quality_band(geometric_coverage[name], 0.25, 0.5, 0.75)
               if supported else "insufficient_data")
        for name in names
    }
    warnings = []
    for name in names:
        if counts[name] < thresholds.min_analysis_features:
            warnings.append(_sift_warning(
                "INSUFFICIENT_FEATURES", "bad",
                f"{name} has too few features for a supported matching/geometry assessment.", images=[name],
            ))
        if feature_status[name] in ("bad", "okay"):
            warnings.append(_sift_warning(
                "LOW_FEATURE_DENSITY", feature_status[name],
                f"{name} has low feature density per megapixel.", images=[name],
            ))
        if supported and geometric_coverage_status[name] in ("bad", "okay"):
            warnings.append(_sift_warning(
                "LOW_CROP_COVERAGE", geometric_coverage_status[name],
                f"{name} has limited geometrically confirmed coverage of the crop; background may contribute.",
                images=[name], cells=_grid_cells_below(
                    distribution[name]["geometric_inlier_counts"], thresholds.min_inliers_per_cell,
                ),
            ))
    if not enough_matches:
        warnings.append(_sift_warning(
            "INSUFFICIENT_MATCHES", "okay",
            "Too few mutual matches for a supported geometry assessment.",
        ))
    elif not geometry_available:
        warnings.append(_sift_warning(
            "GEOMETRY_NOT_AVAILABLE", "okay", "Geometric verification is unavailable.",
        ))
    if matching_quality in ("bad", "okay"):
        warnings.append(_sift_warning(
            "LOW_GEOMETRIC_FEATURE_RATIO", matching_quality,
            "Too few selected strong features survive matching and geometric verification.",
        ))
    if geometry_quality in ("bad", "okay"):
        warnings.append(_sift_warning(
            "LOW_GEOMETRIC_INLIER_RATIO", geometry_quality,
            "Too many mutually matched pairs are geometrically inconsistent.",
        ))
    result["quality"] = {
        "overall_status": _worst_status([feature_quality, matching_quality, geometry_quality]),
        "feature_quality": feature_quality,
        "matching_quality": matching_quality,
        "geometry_quality": geometry_quality,
        "spatial_coverage": _worst_status(list(geometric_coverage_status.values())),
        "metrics": {
            "geometric_feature_ratio": geometric_ratios,
            "feature_coverage": feature_coverage,
            "feature_coverage_status": feature_coverage_status,
            "geometric_coverage": geometric_coverage,
            "geometric_coverage_status": geometric_coverage_status,
        },
        "thresholds": asdict(thresholds),
        "warnings": warnings,
    }
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


def _quality_band(value, bad_threshold, good_threshold, great_threshold) -> str:
    """Classify normalized evidence with inclusive lower category bounds."""
    if value is None:
        return "insufficient_data"
    if value < bad_threshold:
        return "bad"
    if value < good_threshold:
        return "okay"
    if value < great_threshold:
        return "good"
    return "great"


def _worst_status(statuses: list[str]) -> str:
    """A known bad result remains bad; unsupported evidence cannot be good."""
    if "bad" in statuses:
        return "bad"
    if not statuses or "insufficient_data" in statuses:
        return "insufficient_data"
    ranking = {"great": 0, "good": 1, "okay": 2}
    return max(statuses, key=lambda status: ranking[status])


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
        "severity": "error" if status == "bad" else "warning",
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
