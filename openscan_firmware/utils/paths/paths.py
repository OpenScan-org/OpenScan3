"""
Path generation utilities

Provides functions and classes for generating scan paths in both cartesian and polar coordinates.
Supports Fibonacci-based point distributions with optional constraints on the theta and phi angles,
which are typically limited by the degrees of freedom of the scan rig motors.
"""

import abc
import logging

import numpy as np
from typing import Optional

from openscan_firmware.models.paths import CartesianPoint3D, PathMethod, PolarPoint3D

logger = logging.getLogger(__name__)

def polar_to_cartesian(point: PolarPoint3D) -> CartesianPoint3D:
    """Convert polar coordinates to cartesian coordinates"""
    theta_rad = np.radians(point.theta)
    fi_rad = np.radians(point.fi)
    x = point.r * np.sin(theta_rad) * np.cos(fi_rad)
    y = point.r * np.sin(theta_rad) * np.sin(fi_rad)
    z = point.r * np.cos(theta_rad)
    return CartesianPoint3D(x, y, z)


def cartesian_to_polar(point: CartesianPoint3D) -> PolarPoint3D:
    """Convert cartesian coordinates to polar coordinates"""
    r = np.sqrt(point.x ** 2 + point.y ** 2 + point.z ** 2)
    # Handle case where r=0
    if r < 1e-10:
        return PolarPoint3D(0, 0, 0)

    theta = np.degrees(np.arccos(point.z / r))
    # Handle the case where both x and y are zero
    if abs(point.x) < 1e-10 and abs(point.y) < 1e-10:
        fi = 0
    else:
        fi = np.degrees(np.arctan2(point.y, point.x))
        # Convert to range 0-360°
        if fi < 0:
            fi += 360

    return PolarPoint3D(theta, fi, r)


def get_path(method: PathMethod, num_points: int) -> list[CartesianPoint3D]:
    """
    Get path by method and number of points

    Args:
        method: Path generation method
        num_points: Number of points to generate

    Returns:
        List of cartesian points
    """
    if method == PathMethod.FIBONACCI:
        return _PathGeneratorFibonacci.get_path(num_points)
    else:
        logger.error(f"Unknown path method {method}")
        raise ValueError(f"Method {method} not implemented")


def get_polar_path(method: PathMethod, num_points: int) -> list[PolarPoint3D]:
    """
    Get path directly in polar coordinates

    Args:
        method: Path generation method
        num_points: Number of points to generate

    Returns:
        List of polar points
    """
    cartesian_points = get_path(method, num_points)
    return [cartesian_to_polar(point) for point in cartesian_points]


def get_constrained_path(
    method: PathMethod,
    num_points: int,
    min_theta: float = 0,
    max_theta: float = 180,
    min_phi: float = 0,
    max_phi: float = 360,
) -> list[PolarPoint3D]:
    """
    Generate a path within specific theta and phi angle constraints.

    This function generates points specifically within the angle constraints
    rather than filtering from a full sphere, ensuring better distribution.

    Args:
        method: The path generation method to use
        num_points: The target number of points to generate
        min_theta: Minimum theta angle in degrees (default: 0)
        max_theta: Maximum theta angle in degrees (default: 180)
        min_phi: Minimum phi angle in degrees (default: 0)
        max_phi: Maximum phi angle in degrees (default: 360)

    Returns:
        A list of PolarPoint3D objects within the specified constraints
    """
    logger.debug(
        "Generating constrained path for %d points, min theta: %s, max theta: %s, min phi: %s, max phi: %s",
        num_points,
        min_theta,
        max_theta,
        min_phi,
        max_phi,
    )
    # Validate input constraints
    if min_theta < 0 or max_theta > 180:
        logger.error("Theta angle must be between 0° and 180°")
        raise ValueError("Theta angle must be between 0° and 180°")
    if min_theta > max_theta:
        logger.error("Minimum theta angle must be less than or equal to maximum theta angle")
        raise ValueError("Minimum theta angle must be less than or equal to maximum theta angle")
    if min_phi < 0 or min_phi > 360 or max_phi < 0 or max_phi > 360:
        logger.error("Phi angle must be between 0° and 360°")
        raise ValueError("Phi angle must be between 0° and 360°")

    if method == PathMethod.FIBONACCI:
        return _generate_constrained_fibonacci(
            num_points=num_points,
            min_theta=min_theta,
            max_theta=max_theta,
            min_phi=min_phi,
            max_phi=max_phi,
        )
    else:
        logger.error(f"Constrained path generation not implemented for method {method}")
        raise ValueError(f"Constrained path generation not implemented for method {method}")


def _phi_span(min_phi: float, max_phi: float) -> float:
    """Return the positive span of a phi interval, supporting wrap-around at 360°."""
    if min_phi == max_phi:
        return 0.0

    span = (max_phi - min_phi) % 360
    return 360 if span == 0 else span


def _generate_constrained_fibonacci(
    num_points: int,
    min_theta: float,
    max_theta: float,
    min_phi: float,
    max_phi: float,
) -> list[PolarPoint3D]:
    """
    Generate fibonacci points within theta/phi constraints.

    The fibonacci sphere algorithm works by:
    1. Distributing Z values linearly from -1 to 1
    2. Converting Z to theta via theta = arccos(z)
    3. Distributing phi using a golden-ratio sequence within the allowed azimuth range

    To constrain theta, we limit the Z values accordingly.
    To constrain phi, we map the golden-ratio sequence into the requested azimuth span.
    """
    logger.debug(
        "Generating constrained fibonacci path for %d points, min theta: %s, max theta: %s, min phi: %s, max phi: %s",
        num_points,
        min_theta,
        max_theta,
        min_phi,
        max_phi,
    )
    if min_theta == max_theta and min_phi == max_phi:
        return [PolarPoint3D(theta=min_theta, fi=min_phi % 360, r=1.0)]

    # Convert theta constraints to Z constraints
    # theta = arccos(z), so z = cos(theta)
    # Note: theta increases as z decreases
    z_max = np.cos(np.radians(min_theta))  # z at min_theta
    z_min = np.cos(np.radians(max_theta))  # z at max_theta
    phi_span = _phi_span(min_phi, max_phi)

    # Generate fibonacci points within the constrained Z range
    ga = (3 - np.sqrt(5)) * np.pi  # golden angle
    golden_ratio_conjugate = (np.sqrt(5) - 1) / 2

    points = []
    for i in range(num_points):
        # Distribute Z values linearly within the constrained range
        z = z_min + (z_max - z_min) * (i / (num_points - 1)) if num_points > 1 else (z_min + z_max) / 2

        # Calculate radius at this Z level
        radius = np.sqrt(1 - z * z)

        # Calculate fibonacci angle
        theta_fib = ga * i

        # Calculate cartesian coordinates
        x = radius * np.cos(theta_fib)
        y = radius * np.sin(theta_fib)

        # Convert to polar coordinates
        r = 1.0  # unit sphere
        theta = float(np.clip(np.degrees(np.arccos(z)), min_theta, max_theta))
        phi_fraction = (i * golden_ratio_conjugate) % 1
        fi = float((min_phi + phi_span * phi_fraction) % 360)

        points.append(PolarPoint3D(theta, fi, r))

    logger.debug(f"Generated fibonacci path for {num_points} points")
    return points


class _PathGenerator(abc.ABC):
    """Base class for path generators"""

    @staticmethod
    @abc.abstractmethod
    def get_path(num_points: int) -> list[CartesianPoint3D]:
        raise NotImplementedError


class _PathGeneratorFibonacci(_PathGenerator):
    """Fibonacci sphere path generator"""

    @staticmethod
    def get_path(num_points: int) -> list[CartesianPoint3D]:
        logger.debug(f"Generating unconstrained fibonacci path with {num_points} points")
        ga = (3 - np.sqrt(5)) * np.pi  # golden angle
        # Create a list of golden angle increments along the range of number of points
        theta = ga * np.arange(num_points)
        # Z is split into a range of -1 to 1 to create a unit sphere
        z = np.linspace(1 / num_points - 1, 1 - 1 / num_points, num_points)
        # Calculate the radii at each height step of the unit sphere
        radius = np.sqrt(1 - z * z)
        # Determine where xy fall on the sphere, given the azimuthal and polar angles
        y = radius * np.sin(theta)
        x = radius * np.cos(theta)

        return [CartesianPoint3D(x[i], y[i], z[i]) for i in range(len(z))]
