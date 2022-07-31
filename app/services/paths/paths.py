import abc
from enum import Enum

class PathMethod(Enum):
    GRID = "grid"
    FIBONACCI = "fibonacci"
    SPIRAL = "spiral"
    ARCHIMEDES = "archimedes"

class PathGenerator(abc.ABC):
    ...
