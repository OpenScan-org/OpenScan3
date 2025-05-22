from dataclasses import dataclass
from enum import Enum


class PathMethod(Enum):
    FIBONACCI = "fibonacci"
    # Removed SPIRAL and ARCHIMEDES as requested
    # Future methods that can be implemented
    # GRID = "grid"


@dataclass
class CartesianPoint3D:
    x: float
    y: float
    z: float


@dataclass
class PolarPoint3D:
    """
    PolarPoint3D represents a point in spherical coordinates
    theta: polar angle (0° to 180°), where:
        - 0° is the North Pole
        - 90° is the Equator
        - 180° is the South Pole
    fi: azimuthal angle (0° to 360°), rotation around z-axis
    r: radius, default is 1 for unit sphere
    """
    theta: float  # 0° to 180° (pole to pole)
    fi: float  # 0° to 360° (rotation around z-axis)
    r: float = 1
