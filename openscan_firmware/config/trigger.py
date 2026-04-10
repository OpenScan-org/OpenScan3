from __future__ import annotations

from enum import Enum

from pydantic import AliasChoices, BaseModel, Field


class TriggerActiveLevel(str, Enum):
    ACTIVE_HIGH = "active_high"
    ACTIVE_LOW = "active_low"


class TriggerConfig(BaseModel):
    enabled: bool = Field(default=True, description="Whether this trigger can be fired.")
    pin: int = Field(..., ge=0, description="BCM GPIO pin used for the trigger line.")
    active_level: TriggerActiveLevel = Field(
        default=TriggerActiveLevel.ACTIVE_HIGH,
        validation_alias=AliasChoices("active_level", "polarity"),
        description="Defines which logic level is considered active. The idle level is the inverse.",
    )
    pulse_width_ms: int = Field(
        default=100,
        ge=1,
        le=5_000,
        description="How long the trigger line stays active for each trigger pulse in ms.",
    )


# Backwards-compatible alias for older code/config payloads.
TriggerPolarity = TriggerActiveLevel
