from __future__ import annotations

from pydantic import BaseModel, Field

from openscan_firmware.config.scan import ScanSetting
from openscan_firmware.models.paths import PathMethod


class ExternalTriggerRunSettings(BaseModel):
    path_method: PathMethod = Field(
        default=PathMethod.FIBONACCI,
        description="Scanning path generator for the external trigger run.",
    )
    points: int = Field(130, ge=1, le=999, description="Number of trigger positions.")
    min_theta: float = Field(
        12.0,
        ge=0.0,
        le=180.0,
        description="Minimum theta angle in degrees for constrained paths.",
    )
    max_theta: float = Field(
        125.0,
        ge=0.0,
        le=180.0,
        description="Maximum theta angle in degrees for constrained paths.",
    )
    optimize_path: bool = Field(
        True,
        description="Enable path optimization based on the configured motor parameters.",
    )
    optimization_algorithm: str = Field(
        "nearest_neighbor",
        description="Path optimization algorithm to use when optimize_path is enabled.",
    )
    trigger_name: str = Field(
        ...,
        min_length=1,
        description="Name of the configured trigger device to fire at each scan point.",
    )
    pre_trigger_delay_ms: int = Field(
        default=0,
        ge=0,
        le=600_000,
        description="Delay after reaching the scan position and before asserting the trigger.",
    )
    post_trigger_delay_ms: int = Field(
        default=0,
        ge=0,
        le=600_000,
        description="Delay after releasing the trigger before the next scan step starts.",
    )

    def to_scan_settings(self) -> ScanSetting:
        """Adapt the path-related settings to the shared scan path generator."""
        return ScanSetting(
            path_method=self.path_method,
            points=self.points,
            min_theta=self.min_theta,
            max_theta=self.max_theta,
            optimize_path=self.optimize_path,
            optimization_algorithm=self.optimization_algorithm,
            focus_stacks=1,
            focus_range=(10.0, 15.0),
            image_format="jpeg",
        )
