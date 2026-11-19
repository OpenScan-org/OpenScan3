"""API endpoints for scan setup analysis."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from openscan_firmware.config.camera import CameraSettings
from openscan_firmware.controllers.services import scan_analysis as scan_analysis_service
from openscan_firmware.models.scan_analysis import FocusAnalysisResult, NormalizedCrop
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
    applied: CameraSettings = Field(description="Settings active after successful analysis, including restoration of temporary SIFT focus changes.")


class SiftGridSize(BaseModel):
    """Dimensions of the spatial SIFT analysis grid."""

    rows: int
    columns: int


class SiftGridImageMetrics(BaseModel):
    """Per-cell SIFT metrics for one image in the comparison pair."""

    feature_counts: list[list[int]] = Field(description="All detected features per cell in the analysis crop (maximum 1280 pixels on its longest side).")
    matchable_feature_counts: list[list[int]] = Field(description="Mutually matched selected features per cell; the sample contains up to 1000 strongest grid-balanced features per image.")
    geometric_inlier_counts: list[list[int]] | None = Field(description="Selected features per cell surviving geometric verification.")


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


SiftQualityStatus = Literal["bad", "okay", "good", "great", "insufficient_data"]


class SiftQuality(BaseModel):
    """Initial heuristic quality assessment for the SIFT comparison."""

    overall_status: SiftQualityStatus = Field(description="Worst core assessment: density, geometrically usable sample fraction, and geometric consistency. Spatial crop coverage is rated separately.")
    feature_quality: SiftQualityStatus
    spatial_coverage: SiftQualityStatus = Field(description="Distribution of geometrically verified selected features within the rectangular crop, including background. Insufficient data suppresses positive ratings.")
    matching_quality: SiftQualityStatus = Field(description="Geometrically verified fraction of each strongest-feature sample; requires at least 150 features per image and 50 mutual pairs.")
    geometry_quality: SiftQualityStatus = Field(description="Geometric inlier-ratio assessment; insufficient_data when the evidence or geometric model is unavailable.")
    metrics: dict[str, Any]
    thresholds: dict[str, int | float]
    warnings: list[SiftQualityWarning]


class SiftAnalysis(BaseModel):
    """Feature and match quality metrics for the SIFT comparison pair."""

    turntable_angle: float
    feature_count: dict[str, int] = Field(description="All SIFT features detected after cropping and limiting the longest side to 1280 pixels.")
    feature_density: dict[str, float] = Field(description="Detected features per megapixel of the actual analysis crop, after limiting resolution. No foreground/background segmentation is applied.")
    tentative_match_count: int = Field(description="Unique ratio-test candidate pairs from the two strongest-per-cell samples; searched against full descriptor pools.")
    mutual_match_count: int = Field(description="Unique sampled candidate pairs passing ratio tests in both directions.")
    matchable_feature_count: dict[str, int] = Field(description="Mutually matched features among each image's selected sample of min(feature_count, 1000).")
    matchable_feature_ratio: dict[str, float] = Field(description="Matched fraction of each image's strongest grid-balanced sample, not of all detected features.")
    unmatched_feature_count: dict[str, int] = Field(description="Unmatched selected features; untested features are excluded.")
    geometric_inlier_count: int | None = Field(description="Unique sampled mutual pairs surviving geometric verification.")
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
            "Camera settings before analysis and applied after successful completion. "
            "The previous settings can be used for reset."
        ),
    )
    sift_analysis: SiftAnalysis | None = Field(
        default=None,
        description="SIFT feature matching metrics from a small turntable rotation.",
    )
    focus_analysis: FocusAnalysisResult | None = Field(
        default=None,
        description=(
            "Crop-weighted regional ISP focus profiles normalized to the best "
            "measurement per region, including AF. Null if statistics are unavailable."
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
