from datetime import datetime
import json
from dataclasses import field
from pydantic.dataclasses import dataclass

import pathlib


@dataclass
class ProjectManifest:
    date: datetime

    uploaded: bool = False

@dataclass
class Project:
    name: str
    path: pathlib.Path
    
    manifest: ProjectManifest

    photos: list[str] = field(default_factory=list)
