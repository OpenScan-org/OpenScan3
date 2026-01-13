"""Top-level package for OpenScan3 Python distribution."""

__all__ = []

from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("openscan-firmware")  # read from pyproject.toml
except PackageNotFoundError:
    __version__ = "0.0.0-dev"  # fallback for development