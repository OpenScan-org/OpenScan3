from datetime import datetime
import json
from dataclasses import field
import uuid
from pydantic.dataclasses import dataclass
from typing import Tuple


import pathlib


@dataclass
class Project:
    name: str
    path: pathlib.Path

    created: datetime

    focus_stacking: Tuple[int, bool, float, float] = field(default_factory=lambda: (1, True, 0.0, 0.0))

    uploaded: bool = False

    photos: list[str] = field(default_factory=list)
