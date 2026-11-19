"""Models shared by scan setup analysis APIs and tasks."""
from __future__ import annotations

import math

from pydantic import BaseModel, Field, RootModel, model_validator


class FocusRegionResult(BaseModel):
    weight: float = Field(description="Region crop-overlap weight, normalized per position.")
    autofocus: float | None = Field(description="AF ISP focus value relative to the region's best measurement; not absolute sharpness.")
    manual: dict[str, float | None] = Field(description="Relative ISP focus values keyed by requested lens position in diopters. Missing or unnormalizable values are null.")


class FocusPositionResult(BaseModel):
    turntable_offset: float
    autofocus_position: float | None = Field(description="Measured LensPosition of the autofocus capture in diopters; null if unavailable.")
    regions: dict[str, FocusRegionResult] = Field(description="Crop-overlapping ISP regions keyed by region index.")


class StackingOption(BaseModel):
    focus_range: tuple[float, float]
    focus_stacks: int = Field(ge=2, le=10)
    coverage: float = Field(description="Mean crop-weighted relative ISP sharpness over sampled views; not a thresholded area percentage.")
    worst_coverage: float = Field(description="Lowest sampled-view crop-weighted relative sharpness score.")
    gain: float | None = Field(description="Mean score minus mean AF score; null when AF measurements are incomplete.")
    estimated: bool = Field(description="True if any selected focus value requires interpolation at any sampled region/view.")


class FocusAnalysisResult(BaseModel):
    positions: list[FocusPositionResult]
    stacking_options: list[StackingOption]


class NormalizedCrop(RootModel[tuple[float, float, float, float]]):
    """Crop rectangle as normalized ``x, y, width, height`` values.

    A root model keeps the existing compact JSON representation as an array
    while moving validation out of the task implementation.
    """

    @model_validator(mode="after")
    def validate_geometry(self) -> "NormalizedCrop":
        x, y, width, height = self.root
        if not all(math.isfinite(value) for value in self.root):
            raise ValueError("crop values must be finite numbers.")
        if width <= 0 or height <= 0:
            raise ValueError("crop width and height must be greater than zero.")
        if x + width > 1 or y + height > 1:
            raise ValueError("crop must be contained in the normalized image frame.")
        return self

    @property
    def as_tuple(self) -> tuple[float, float, float, float]:
        """Return the crop in the representation used by image utilities."""
        return self.root
