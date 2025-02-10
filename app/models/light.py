from dataclasses import dataclass
from enum import Enum
from typing import Optional

from app.config.light import LightConfig


@dataclass
class Light:
    name: str
    #turned_on: bool
    settings: Optional[LightConfig]