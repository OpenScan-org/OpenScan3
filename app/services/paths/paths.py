import abc
import io
from dataclasses import dataclass

import matplotlib.pyplot as plt
import numpy as np
import itertools

from app.models.paths import CartesianPoint3D, PathMethod, PolarPoint3D


def polar_to_cartesian(point: PolarPoint3D) -> CartesianPoint3D:
    ...


def cartesian_to_polar(point: CartesianPoint3D) -> PolarPoint3D:
    r = 1
    theta = np.degrees(np.arccos(point.z / r))
    fi = np.degrees(np.arctan(point.x / point.y))
    return PolarPoint3D(theta, fi, r)


def get_path(method: PathMethod, num_points: int) -> list[CartesianPoint3D]:
    if method == PathMethod.GRID:
        return PathGeneratorGrid.get_path(num_points)
    elif method == PathMethod.FIBONACCI:
        return PathGeneratorFibonacci.get_path(num_points)
    elif method == PathMethod.SPIRAL:
        return PathGeneratorSpiral.get_path(num_points)
    elif method == PathMethod.ARCHIMEDES:
        return PathGeneratorArchimedes.get_path(num_points)


def plot_points(points: list[CartesianPoint3D], index = None) -> bytes:
    fig = plt.figure()
    ax = fig.add_subplot(111, projection="3d")
    colors = None
    if (index is not None and (1<= int(index) <= len(points))):
        colors = [i for i in itertools.repeat([0,0,1,0.1], len(points))]
        colors[int(index)-1] = [1,0,0]
        

    ax.scatter([p.x for p in points], [p.y for p in points], [p.z for p in points], color = colors)
    with io.BytesIO() as f:
        plt.savefig(f)
        return f.getvalue()


class PathGenerator(abc.ABC):
    @abc.abstractstaticmethod
    def get_path(num_points: int) -> list[CartesianPoint3D]:
        raise NotImplementedError


class PathGeneratorGrid(PathGenerator):
    def get_path(num_points: int) -> list[CartesianPoint3D]:
        return []


#  fibonacci sphere based on method by Seahmatthews
#  on https://gist.github.com/Seanmatthews/a51ac697db1a4f58a6bca7996d75f68c
class PathGeneratorFibonacci(PathGenerator):
    def get_path(num_points: int) -> list[CartesianPoint3D]:
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

        return [CartesianPoint3D(x[i], y[i], z[i]) for i in range(len(z))]


class PathGeneratorSpiral(PathGenerator):
    def get_path(num_points: int) -> list[CartesianPoint3D]:
        a = 0.05
        r = 1
        t = np.linspace(1 / num_points - 30, 30 - 1 / num_points, num_points)
        # Determine where xy fall on the sphere, given the azimuthal and polar angles
        x = r * np.cos(t) / np.sqrt(a**2 * t**2 + 1)
        y = r * np.sin(t) / np.sqrt(a**2 * t**2 + 1)
        z = -(a * r * t) / np.sqrt(a**2 * t**2 + 1)

        return [CartesianPoint3D(x[i], y[i], z[i]) for i in range(len(z))]


class PathGeneratorArchimedes(PathGenerator):
    def get_path(num_points: int) -> list[CartesianPoint3D]:
        return []
