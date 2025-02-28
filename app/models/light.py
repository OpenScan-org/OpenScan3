from dataclasses import dataclass
from enum import Enum
from typing import Optional

from app.config.light import LightConfig


@dataclass
class Light:
    name: str

    settings: LightConfig
    settings_file: Optional[str] = None
