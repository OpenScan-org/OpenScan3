from pydantic import BaseModel, Field
from typing import Tuple

from app.models.paths import PathMethod

class ScanSetting(BaseModel):
    path_method: PathMethod
    points: int
    focus_stacks: int = 1
    focus_range: Tuple[float, float] = Field(default=(0.0, 0.0))

