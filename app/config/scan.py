from pydantic import BaseModel, Field, confloat
from typing import Tuple

from app.models.paths import PathMethod

class ScanSetting(BaseModel):
    path_method: PathMethod
    points: int = Field(130, ge=1, le=999, description="Number of points in scanning path.")
    focus_stacks: int = Field(1, ge=1, le=99, description="Number of photos with different focus per position."
                                                          "This ignores AF and you need to set a focus range."
                                                          "Focus values will then be evenly spaced between min and max.")
    focus_range: Tuple[
        confloat(ge=0.0, le=15.0),
        confloat(ge=0.0, le=15.0)] = Field(default=(10.0, 15.0),
                                           description="Minimum and maximum focus distance in diopters.")

