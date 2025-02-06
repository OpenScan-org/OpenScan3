from dataclasses import dataclass
from typing import Optional, List


@dataclass
class LightConfig:
    name: str
    pin: Optional[int] = None
    pins: Optional[List[int]] = None
    pwm: bool = False
