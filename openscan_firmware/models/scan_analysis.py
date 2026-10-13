"""Models shared by scan setup analysis APIs and tasks."""
from __future__ import annotations

import math

from pydantic import RootModel, model_validator


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
