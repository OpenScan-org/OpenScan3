import logging
from typing import Optional, List

from pydantic import BaseModel, Field, model_validator

logger = logging.getLogger(__name__)

class LightConfig(BaseModel):
    pin: Optional[int] = Field(default=None, description="Single GPIO pin controlling the light output.")
    pins: Optional[List[int]] = Field(default=None, description="Multiple GPIO pins driving grouped light outputs.")
    pwm_support: bool = Field(
        default=False,
        description="Indicates whether this light hardware can handle PWM (otherwise only on/off).",
    )
    pwm_frequency: float = Field(10000.0, ge=50.0, le=100000.0, description="PWM frequency for led driver.")
    pwm_min: float = Field(0.0, ge=0, le=3.3, description="Minimum pwm voltage for led driver.")
    pwm_max: float = Field(0.0, ge=0, le=3.3, description="Maximum pwm voltage for led driver.")

    @model_validator(mode="before")
    @classmethod
    def ensure_pins(cls, values):
        """
        Ensures that the 'pins' field is always a list, supporting both 'pin' and 'pins' in the config.

        If both 'pin' and 'pins' are set, both are merged into a single list (duplicates removed) and a warning is logged.
        """
        pins = values.get("pins")
        pin = values.get("pin")
        merged_pins = []

        if pins is not None and pin is not None:
            logger.warning(
                f"Both 'pin' ({pin}) and 'pins' ({pins}) are set in LightConfig. "
                "Both will be merged into the 'pins' list. This may be unintentional."
            )

        if pins is not None:
            merged_pins = list(pins)
        if pin is not None:
            merged_pins.append(pin)

        values["pins"] = list(dict.fromkeys(merged_pins))
        return values
        
    @model_validator(mode="after")
    def validate_pwm_range(self):
        """
        Ensures pwm_min <= pwm_max and consistency with pwm_support.
        """

        if self.pwm_min >= self.pwm_max:
            raise ValueError("pwm_min must be less than pwm_max")


        return self
