import logging
from pydantic import BaseModel, model_validator
from typing import Optional, List

logger = logging.getLogger(__name__)

class LightConfig(BaseModel):
    pin: Optional[int] = None
    pins: Optional[List[int]] = None
    pwm: bool = False

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