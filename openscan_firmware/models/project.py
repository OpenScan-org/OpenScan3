from datetime import datetime
import pathlib
import unicodedata
from typing import ClassVar, Optional

from pydantic import BaseModel, Field, field_validator

from openscan_firmware.models.scan import Scan


class Project(BaseModel):
    """Represents a scan project stored on disk and optionally processed in the cloud."""

    name: str = Field(
        description="Name of the project."
    )
    path: str = Field(
        description="Path to the project directory."
    )

    created: datetime = Field(
        description="Creation timestamp of the project."
    )

    scans: dict[str, Scan] = Field(
        description="Scans associated with the project."
    )

    description: Optional[str] = Field(
        default=None,
        description="Description of the project."
    )
    uploaded: bool = Field(
        default=False,
        description="Whether the model has been uploaded to the cloud."
    )
    cloud_project_name: Optional[str] = None
    downloaded: bool = Field(
        default=False,
        description="Whether the model has been downloaded from the cloud."
    )


    # Constants for Validation
    MAX_NAME_LENGTH: ClassVar[int] = 150  # ensures compatibility with older Microsoft Windows versions
    FORBIDDEN_CHARACTERS: ClassVar[set[str]] = {"/", "\\", ":", "*", "?", '"', "<", ">", "|"}
    ALLOWED_PUNCTUATION: ClassVar[set[str]] = {"_", "-", ".", "'"}
    WINDOWS_RESERVED_NAMES: ClassVar[set[str]] = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }

    @field_validator('name')
    def validate_name(cls, name: str) -> str:
        """Validate the name of the project which will be used as directory name

        Args:
            name: To be validated

        Returns:
            str: The validated name

        Raises:
            ValueError: If name is invalid
        """
        normalized_name = unicodedata.normalize('NFC', name)
        stripped_name = normalized_name.strip()

        if len(stripped_name) == 0:
            raise ValueError("The name of the project cannot be empty or whitespace only.")

        # Check maximum length
        if len(normalized_name) > cls.MAX_NAME_LENGTH:
            raise ValueError(
                f"The name should not exceed {cls.MAX_NAME_LENGTH} characters."
            )

        if normalized_name[0] in {" ", "."} or normalized_name[-1] in {" ", "."}:
            raise ValueError("The project name must not start or end with a space or period.")

        upper_name = stripped_name.upper()
        if upper_name in cls.WINDOWS_RESERVED_NAMES:
            raise ValueError("The project name cannot be one of the reserved Windows names (CON, PRN, AUX, NUL, COM1-9, LPT1-9).")

        for character in normalized_name:
            category = unicodedata.category(character)

            if character in cls.FORBIDDEN_CHARACTERS:
                raise ValueError(f"Character '{character}' is not allowed in project names.")

            if category.startswith('C'):
                raise ValueError("Control characters are not allowed in project names.")

            if category[0] in {"L", "N"}:  # Letters and numbers
                continue

            if character in cls.ALLOWED_PUNCTUATION:
                continue

            if category == "Zs":  # space separator
                continue

            raise ValueError(
                "The project name contains unsupported characters. Allowed are letters, numbers, spaces, hyphen, underscore, period, and apostrophe."
            )

        return normalized_name


    @field_validator('path')
    def validate_path(cls, path: str) -> str:
        """Validate and normalize the path"""
        try:
            path_obj = pathlib.Path(path)
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