from datetime import datetime
import re
from pydantic import BaseModel, validator
from pydantic.dataclasses import dataclass
from typing import ClassVar, Optional

from app.models.scan import Scan

import pathlib


class Project(BaseModel):
    name: str
    path: str

    created: datetime

    scans: dict[str, Scan]

    description: Optional[str] = None
    uploaded: bool = False


    # Constants for Validation
    MAX_NAME_LENGTH: ClassVar[int] = 150  # ensures compatability with older Microsoft Windows versions
    VALID_NAME_PATTERN: ClassVar[re.Pattern] = re.compile(r'^[a-zA-Z0-9_. ][a-zA-Z0-9_\-. ]+[a-zA-Z0-9]$') # Allowed characters and patterns

    @validator('name')
    def validate_name(cls, name: str) -> str:
        """Validate the name of the project which will be used as directory name

        Args:
            name: To be validated

        Returns:
            str: The validated name

        Raises:
            ValueError: If name is invalid
        """
        # check maximum length
        if len(name) > cls.MAX_NAME_LENGTH:
            raise ValueError(
                f"The name should not exceed {cls.MAX_NAME_LENGTH} characters."
            )

        if len(name) < 1:
            raise ValueError("The name of the project cannot be empty.")

        # Check for invalid characters or patterns
        if not cls.VALID_NAME_PATTERN.match(name):
            raise ValueError(
                "The project name should only contain characters, numbers, underscores, hyphens and periods. "
                "It must not start with an hyphen."
                "Er muss mit einem Buchstaben oder einer Zahl beginnen und enden."
            )

        return name


    @validator('path')
    def validate_path(cls, path: str) -> str:
        """Validate and normalize the path"""
        try:
            path_obj = pathlib.Path(path)
            # Resolve macht den Pfad absolut und normalisiert ihn
            return str(path_obj.resolve())
        except Exception as e:
            raise ValueError(f"Invalid path: {e}")

    @property
    def path_obj(self) -> pathlib.Path:
        """Get path as Path object when needed"""
        return pathlib.Path(self.path)

    def exists(self) -> bool:
        """Check if project directory exists"""
        return self.path_obj.exists()

    def create_directory(self) -> None:
        """Create project directory if it doesn't exist"""
        self.path_obj.mkdir(parents=True, exist_ok=True)