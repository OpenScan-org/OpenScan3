"""API endpoints for scan setup analysis."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from openscan_firmware.config.camera import CameraSettings
from openscan_firmware.controllers.services import scan_analysis as scan_analysis_service
from openscan_firmware.models.scan_analysis import NormalizedCrop
from openscan_firmware.models.task import Task


router = APIRouter(prefix="/scan-analysis", tags=["scan_analysis"])


class ScanAnalysisRequest(BaseModel):
    """Parameters for a scan setup analysis capture."""

    camera_name: str = Field(
        min_length=1,
        description="Name of the camera used for the analysis capture.",
    )
    depends_on: str | None = Field(
        default=None,
        description="Optional task ID that must complete successfully before this task runs.",
    )
    debug_images: bool = Field(
        default=False,
        description="Include compact ROI debug images in the result.",
    )
    crop: NormalizedCrop | None = Field(
        default=None,
        description=(
            "Optional normalized x, y, width, height crop in the oriented "
            "full analysis frame."
        ),
    )


class CropRecommendation(BaseModel):
    """Recommended crop values for the detected object extent."""

    coordinate_system: Literal["oriented_full_analysis_frame"] | None = Field(
        default=None,
        description="Coordinate system used by bbox and rect.",
    )
    frame_size: dict[str, int] | None = Field(
        default=None,
        description="Width and height of the full oriented analysis frame.",
    )
    bbox: list[int] | None = Field(
        description="Detected object bounding box as x, y, width, height.",
    )
    bbox_normalized: list[float] | None = Field(
        default=None,
        description="Detected object bounding box relative to the full analysis frame.",
    )
    rect: list[int] | None = Field(
        description="Centered crop rectangle as x, y, width, height.",
    )
    rect_normalized: list[float] | None = Field(
        default=None,
        description="Centered crop rectangle relative to the full analysis frame.",
    )
    crop_width: float | None = Field(
        description="Percentage of image width that can be cropped.",
    )
    crop_height: float | None = Field(
        description="Percentage of image height that can be cropped.",
    )
    support_exclusion: dict[str, Any] | None = Field(
        default=None,
        description="Geometry based exclusion of a lower turntable support.",
    )


class PositionAnalysis(BaseModel):
    """Indicators for uneven object placement in the image."""

    vertical_imbalance: float = Field(
        description="Relative imbalance between the upper and lower image areas.",
    )
    horizontal_imbalance: float = Field(
        description="Relative imbalance between the left and right image areas.",
    )
    vertical_assessment: dict[str, str] = Field(
        description="Qualitative guidance for vertical alignment and holder setup.",
    )
    horizontal_assessment: dict[str, str] = Field(
        description="Qualitative guidance for horizontal object centering.",
    )
    overall_status: Literal["good", "warning"] = Field(
        description="Overall qualitative assessment of object placement.",
    )


class CameraSettingsResult(BaseModel):
    """Camera settings before and after scan analysis."""

    previous: CameraSettings
    recommended: CameraSettings
    applied: bool


class SiftGridSize(BaseModel):
    """Dimensions of the spatial SIFT analysis grid."""

    rows: int
    columns: int


class SiftGridImageMetrics(BaseModel):
    """Per-cell SIFT metrics for one image in the comparison pair."""

    feature_counts: list[list[int]]
    matchable_feature_counts: list[list[int]]
    geometric_inlier_counts: list[list[int]] | None


class SiftSpatialDistribution(BaseModel):
    """Spatial distribution of SIFT features and matches in both images."""

    grid_size: SiftGridSize
    first: SiftGridImageMetrics
    second: SiftGridImageMetrics


class SiftQualityWarning(BaseModel):
    """Structured warning from the qualitative SIFT assessment."""

    code: str
    severity: Literal["warning", "error"]
    message: str
    images: list[Literal["first", "second"]] | None = None
    cells: list[list[int]] | None = None


class SiftQuality(BaseModel):
    """Initial heuristic quality assessment for the SIFT comparison."""

    overall_status: Literal["good", "warning", "poor"]
    feature_quality: Literal["good", "warning", "poor"]
    spatial_coverage: Literal["good", "warning", "poor"]
    matching_quality: Literal["good", "warning", "poor"]
    metrics: dict[str, Any]
    thresholds: dict[str, int | float]
    warnings: list[SiftQualityWarning]


class SiftAnalysis(BaseModel):
    """Feature and match quality metrics for the SIFT comparison pair."""

    turntable_angle: float
    feature_count: dict[str, int]
    tentative_match_count: int
    mutual_match_count: int
    matchable_feature_count: dict[str, int]
    matchable_feature_ratio: dict[str, float]
    unmatched_feature_count: dict[str, int]
    geometric_inlier_count: int | None
    geometric_outlier_count: int | None
    geometric_inlier_ratio: float | None
    spatial_distribution: SiftSpatialDistribution
    quality: SiftQuality | None = None


class SiftBenchmark(BaseModel):
    """Paths to the unscaled RGB frames used for SIFT benchmarking."""

    first_image: str
    second_image: str


class ScanAnalysisResult(BaseModel):
    """Public result of the scan setup analysis."""

    analysis_type: Literal["scan_analysis"]
    crop_recommendation: CropRecommendation | None = None
    position_analysis: PositionAnalysis
    camera_settings: CameraSettingsResult | None = Field(
        default=None,
        description=(
            "Previous and recommended camera settings. The recommended settings "
            "are active after a successful analysis and the previous settings can "
            "be used for reset."
        ),
    )
    sift_analysis: SiftAnalysis | None = Field(
        default=None,
        description="SIFT feature matching metrics from a small turntable rotation.",
    )
    focus_analysis: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Regional ISP focus measurements and a preliminary autofocus or "
            "focus-stacking assessment."
        ),
    )
    sift_benchmark: SiftBenchmark | None = Field(
        default=None,
        description="Temporary paths to unscaled SIFT benchmark frames.",
    )
    debug_images: dict[str, Any] | None = Field(
        default=None,
        description="Optional diagnostic images requested with debug_images.",
    )


class ScanAnalysisTaskResponse(Task):
    """Task envelope with the typed scan analysis result."""

    result: ScanAnalysisResult | None = None


@router.post(
    "",
    response_model=ScanAnalysisTaskResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_scan_analysis(request: ScanAnalysisRequest) -> ScanAnalysisTaskResponse:
    """Start a scan setup analysis task."""
    try:
        return await scan_analysis_service.start_scan_analysis(
            camera_name=request.camera_name,
            depends_on=request.depends_on,
            debug_images=request.debug_images,
            crop=request.crop,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
