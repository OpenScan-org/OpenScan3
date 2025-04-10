from pydantic import BaseModel
from typing import Optional, List


class LightConfig(BaseModel):
    name: str
    pin: Optional[int] = None
    pins: Optional[List[int]] = None
    pwm: bool = False
