from dataclasses import dataclass
from enum import Enum
from typing import Optional

from app.config.light import LightConfig

class LightType(Enum):
    RINGLIGHT = "ringlight"

@dataclass
class Light:
    settings: Optional[LightConfig]