from __future__ import annotations

from datetime import datetime

from pydantic import AliasChoices, BaseModel, Field

from openscan_firmware.models.paths import CartesianPoint3D, PolarPoint3D


class ExternalTriggerPoint(BaseModel):
    execution_step: int
    original_step: int
    polar_coordinates: PolarPoint3D
    cartesian_coordinates: CartesianPoint3D


class ExternalTriggerRunPath(BaseModel):
    task_id: str = Field(validation_alias=AliasChoices("task_id", "run_id"))
    generated_at: datetime = Field(default_factory=datetime.now)
    total_steps: int = Field(0, ge=0)
    points: list[ExternalTriggerPoint] = Field(default_factory=list)
