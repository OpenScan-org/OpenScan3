from dataclasses import dataclass
from enum import Enum


class PathMethod(Enum):
    GRID = "grid"
    FIBONACCI = "fibonacci"
    SPIRAL = "spiral"
    ARCHIMEDES = "archimedes"


@dataclass
class CartesianPoint3D:
    x: float
    y: float
    z: float


@dataclass
class PolarPoint3D:
    theta: float
    fi: float
    r: float = 1