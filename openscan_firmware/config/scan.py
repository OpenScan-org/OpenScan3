from pydantic import BaseModel, Field, confloat
from typing import Tuple, Literal

from openscan_firmware.models.paths import PathMethod


class ScanSetting(BaseModel):
    path_method: PathMethod
    points: int = Field(130, ge=1, le=999, description="Number of points in scanning path.")

    image_format: Literal['jpeg','dng','rgb_array', 'yuv_array']

    # Theta constraints for constrained paths
    min_theta: float = Field(12.0, ge=0.0, le=180.0,
                             description="Minimum theta angle in degrees for constrained paths.")
    max_theta: float = Field(125.0, ge=0.0, le=180.0,
                             description="Maximum theta angle in degrees for constrained paths.")

    # Path optimization settings
    optimize_path: bool = Field(True, description="Enable path optimization for faster scanning.")
    optimization_algorithm: str = Field("nearest_neighbor", description="Path optimization algorithm to use.")

    focus_stacks: int = Field(1, ge=1, le=99, description="Number of photos with different focus per position."
                                                          "This ignores AF and you need to set a focus range."
                                                          "Focus values will then be evenly spaced between min and max.")
    focus_range: Tuple[
        confloat(ge=0.0, le=15.0),
        confloat(ge=0.0, le=15.0)] = Field(default=(10.0, 15.0),
                                           description="Minimum and maximum focus distance in diopters.")

    @property
    def focus_positions(self) -> list[float]:
        """Calculate focus positions for focus stacking.
        
        Returns:
            List of focus positions from min_focus to max_focus.
            Empty list if focus_stacks <= 1.
        """
        if self.focus_stacks <= 1:
            return []
        
        min_focus, max_focus = self.focus_range
        return [
            min_focus + i * (max_focus - min_focus) / (self.focus_stacks - 1)
            for i in range(self.focus_stacks)
        ]