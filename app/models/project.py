from datetime import datetime
import json
from dataclasses import field
import uuid
from pydantic.dataclasses import dataclass

import pathlib


@dataclass
class Project:
    name: str
    path: pathlib.Path
    
    created: datetime
    uploaded: bool = False

    photos: list[str] = field(default_factory=list)
