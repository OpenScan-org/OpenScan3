import abc
import io
from dataclasses import dataclass
from enum import Enum

import matplotlib.pyplot as plt
import numpy as np


class PathMethod(Enum):
    GRID = "grid"
    FIBONACCI = "fibonacci"
    SPIRAL = "spiral"
    ARCHIMEDES = "archimedes"


@dataclass
class Point3D:
    x: float
    y: float
    z: float


@dataclass
class PointPolar:
    r: float
    a: float


def polar_to_cartesian(point: PointPolar) -> Point3D:
    ...


def cartesian_to_polar(point: Point3D) -> PointPolar:
    ...


def get_path(method: PathMethod, num_points: int) -> list[Point3D]:
    if method == PathMethod.GRID:
        return PathGeneratorGrid.get_path(num_points)
    elif method == PathMethod.FIBONACCI:
        return PathGeneratorFibonacci.get_path(num_points)
    elif method == PathMethod.SPIRAL:
        return PathGeneratorSpiral.get_path(num_points)
    elif method == PathMethod.ARCHIMEDES:
        return PathGeneratorArchimedes.get_path(num_points)


def plot_points(points: list[Point3D]) -> io.BytesIO:
    fig = plt.figure()
    ax = fig.add_subplot(111, projection="3d")
    ax.plot([p.x for p in points], [p.y for p in points], [p.z for p in points])
    with io.BytesIO() as f:
        plt.savefig(f)
        return f.getvalue()


class PathGenerator(abc.ABC):
    @abc.abstractstaticmethod
    def get_path(num_points: int) -> list[Point3D]:
        raise NotImplementedError


class PathGeneratorGrid(PathGenerator):
    def get_path(num_points: int) -> list[Point3D]:
        return []


#  fibonacci sphere based on method by Seahmatthews
#  on https://gist.github.com/Seanmatthews/a51ac697db1a4f58a6bca7996d75f68c
class PathGeneratorFibonacci(PathGenerator):
    def get_path(num_points: int) -> list[Point3D]:
        ga = (3 - np.sqrt(5)) * np.pi  # golden angle
        # Create a list of golden angle increments along tha range of number of points
        theta = ga * np.arange(num_points)
        # Z is a split into a range of -1 to 1 in order to create a unit circle
        z = np.linspace(1 / num_points - 1, 1 - 1 / num_points, num_points)
        # a list of the radii at each height step of the unit circle
        radius = np.sqrt(1 - z * z)
        # Determine where xy fall on the sphere, given the azimuthal and polar angles
        y = radius * np.sin(theta)
        x = radius * np.cos(theta)

        return [Point3D(x[i], y[i], z[i]) for i in range(len(z))]


class PathGeneratorSpiral(PathGenerator):
    def get_path(num_points: int) -> list[Point3D]:
        return []


class PathGeneratorArchimedes(PathGenerator):
    def get_path(num_points: int) -> list[Point3D]:
        return []
