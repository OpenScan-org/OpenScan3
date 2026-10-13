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


def analyze_cropping_roi(
    focus_series: dict[str, list[AnalysisFrame]],
    *,
    captured_frames: list[tuple[AnalysisFrame, dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Analyze crop suitability and object position from focus series.

    The object position is reported through vertical and horizontal imbalance
    values calculated from the cropping ROI. When ``captured_frames`` is
    provided, compact debug images are included as well.
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
    crop_recommendation = _crop_recommendation(object_mask)
    position_analysis = _calculate_position_imbalance(object_mask)

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


def _calculate_position_imbalance(mask: np.ndarray) -> dict[str, float]:
    """Calculate vertical and horizontal object distribution imbalance."""
    height, width = mask.shape
    half_height = height // 2
    half_width = width // 2
    top = float(mask[:half_height].mean())
    bottom = float(mask[half_height:].mean())
    left = float(mask[:, :half_width].mean())
    right = float(mask[:, half_width:].mean())
    return {
        "vertical_imbalance": round(_imbalance(top, bottom), 6),
        "horizontal_imbalance": round(_imbalance(left, right), 6),
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
