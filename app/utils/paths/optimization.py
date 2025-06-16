"""
Path optimization module for OpenScan3

Provides algorithms to optimize scanning paths for minimal execution time.
The primary algorithm implemented is Nearest Neighbor TSP heuristic.
"""
import logging
import math
from typing import List, Optional, Tuple
from app.models.paths import PolarPoint3D

logger = logging.getLogger(__name__)

class PathOptimizer:
    """Path optimization using various algorithms"""

    def __init__(self, rotor_spr: int, rotor_acceleration: int, rotor_max_speed: int,
                 turntable_spr: int, turntable_acceleration: int, turntable_max_speed: int):
        """
        Initialize path optimizer with motor parameters

        Args:
            rotor_spr: Rotor steps per rotation
            rotor_acceleration: Rotor acceleration (steps/s²)
            rotor_max_speed: Rotor maximum speed (steps/s)
            turntable_spr: Turntable steps per rotation
            turntable_acceleration: Turntable acceleration (steps/s²)
            turntable_max_speed: Turntable maximum speed (steps/s)
        """
        # Cache motor parameters for performance
        self._rotor_spr = rotor_spr
        self._rotor_accel = rotor_acceleration
        self._rotor_speed = rotor_max_speed

        self._turntable_spr = turntable_spr
        self._turntable_accel = turntable_acceleration
        self._turntable_speed = turntable_max_speed

        logger.debug(f"Rotor spr: {self._rotor_spr}, accel: {self._rotor_accel}, speed: {self._rotor_speed}")
        logger.debug(f"Turntable spr: {self._turntable_spr}, accel: {self._turntable_accel}, speed: {self._turntable_speed}")

    def optimize_path(self, points: List[PolarPoint3D],
                     algorithm: str = "nearest_neighbor",
                     start_position: Optional[PolarPoint3D] = None) -> List[PolarPoint3D]:
        """
        Optimize path using specified algorithm

        Args:
            points: List of points to optimize
            algorithm: Optimization algorithm ("nearest_neighbor", "none")
            start_position: Starting position for optimization (defaults to 90°, 0°)

        Returns:
            Optimized list of points
        """
        if not points:
            logger.debug("No points to optimize, returning empty list")
            return []

        if algorithm == "none" or algorithm is None:
            logger.debug("No optimization algorithm specified, returning original path")
            return points.copy()

        if algorithm == "nearest_neighbor":
            return self._nearest_neighbor_tsp(points, start_position)

        logger.error(f"Unknown optimization algorithm: {algorithm}")
        raise ValueError(f"Unknown optimization algorithm: {algorithm}")

    def calculate_path_time(self, points: List[PolarPoint3D],
                           start_position: Optional[PolarPoint3D] = None) -> Tuple[float, List[float]]:
        """
        Calculate total execution time for a path

        Args:
            points: List of points in the path
            start_position: Starting position (defaults to 90°, 0°)

        Returns:
            Tuple of (total_time, individual_move_times)
        """
        logger.debug("Calculating total execution time")
        if not points:
            logger.debug("No points to optimize, returning empty list")
            return 0.0, []

        if start_position is None:
            logger.debug("No start position specified, defaulting to theta 90°, fi 0°")
            start_position = PolarPoint3D(theta=90.0, fi=0.0, r=1.0)

        total_time = 0.0
        move_times = []
        current_pos = start_position

        for point in points:
            move_time = self._calculate_move_time(current_pos, point)
            move_times.append(move_time)
            total_time += move_time
            current_pos = point

        logger.debug(f"Total time: {total_time}, move times: {move_times}")
        return total_time, move_times

    def _nearest_neighbor_tsp(self, points: List[PolarPoint3D],
                             start_position: Optional[PolarPoint3D] = None) -> List[PolarPoint3D]:
        """
        Traveling Salesman Problem - Nearest Neighbor heuristic
        Always move to the nearest unvisited point

        Args:
            points: List of points to optimize
            start_position: Starting position (defaults to 90°, 0°)

        Returns:
            Optimized path
        """
        logger.debug("Optimizing path: Running nearest neighbor TSP")
        if start_position is None:
            start_position = PolarPoint3D(theta=90.0, fi=0.0, r=1.0)

        unvisited = points.copy()
        optimized_path = []
        current_pos = start_position

        while unvisited:
            # Find nearest unvisited point
            nearest_point = None
            min_time = float('inf')

            for point in unvisited:
                move_time = self._calculate_move_time(current_pos, point)
                if move_time < min_time:
                    min_time = move_time
                    nearest_point = point

            # Move to nearest point
            if nearest_point is not None:
                optimized_path.append(nearest_point)
                unvisited.remove(nearest_point)
                current_pos = nearest_point

        logger.debug(f"Optimized path with nearest neighbor: {optimized_path}")
        return optimized_path

    def _calculate_move_time(self, from_point: PolarPoint3D, to_point: PolarPoint3D) -> float:
        """
        Calculate time to move from one point to another

        Args:
            from_point: Starting point
            to_point: Destination point

        Returns:
            Movement time in seconds
        """
        # Rotor movement (theta)
        rotor_degrees = abs(to_point.theta - from_point.theta)
        rotor_time = self._calculate_movement_time_degrees(
            rotor_degrees, self._rotor_spr, self._rotor_accel, self._rotor_speed
        )

        # Turntable movement (fi) - use shortest path with wraparound
        turntable_direct = abs(to_point.fi - from_point.fi)
        turntable_wraparound = 360 - turntable_direct
        turntable_degrees = min(turntable_direct, turntable_wraparound)
        turntable_time = self._calculate_movement_time_degrees(
            turntable_degrees, self._turntable_spr, self._turntable_accel, self._turntable_speed
        )

        # Both motors move concurrently, so time is the maximum
        return max(rotor_time, turntable_time)

    def _calculate_movement_time_degrees(self, degrees: float, steps_per_rotation: int,
                                       acceleration: int, max_speed: int) -> float:
        """
        Calculate movement time for a given number of degrees using trapezoidal motion profile

        Args:
            degrees: Degrees to move
            steps_per_rotation: Steps per 360° rotation
            acceleration: Motor acceleration (steps/s²)
            max_speed: Maximum motor speed (steps/s)

        Returns:
            Movement time in seconds
        """
        if degrees <= 0:
            return 0.0

        steps = int(abs(degrees) * steps_per_rotation / 360)
        if steps == 0:
            return 0.0

        # Calculate acceleration distance (steps)
        accel_time = max_speed / acceleration
        accel_steps = int(0.5 * acceleration * accel_time * accel_time)

        # Check if we can reach max speed (trapezoidal vs. triangular profile)
        if 2 * accel_steps > steps:
            # Triangular profile - never reach max speed
            accel_steps = steps // 2
            if accel_steps < 1:
                accel_steps = 1
            peak_time = math.sqrt(2 * accel_steps / acceleration)
            total_time = 2 * peak_time
        else:
            # Trapezoidal profile - we reach max speed
            const_steps = steps - (2 * accel_steps)
            accel_time = max_speed / acceleration
            const_time = const_steps / max_speed if const_steps > 0 else 0
            decel_time = accel_time
            total_time = accel_time + const_time + decel_time

        return total_time


def optimize_polar_path(points: List[PolarPoint3D],
                       rotor_spr: int, rotor_acceleration: int, rotor_max_speed: int,
                       turntable_spr: int, turntable_acceleration: int, turntable_max_speed: int,
                       algorithm: str = "nearest_neighbor",
                       start_position: Optional[PolarPoint3D] = None) -> List[PolarPoint3D]:
    """
    Convenience function to optimize a polar path

    Args:
        points: List of polar points to optimize
        rotor_spr: Rotor steps per rotation
        rotor_acceleration: Rotor acceleration (steps/s²)
        rotor_max_speed: Rotor maximum speed (steps/s)
        turntable_spr: Turntable steps per rotation
        turntable_acceleration: Turntable acceleration (steps/s²)
        turntable_max_speed: Turntable maximum speed (steps/s)
        algorithm: Optimization algorithm
        start_position: Starting position for optimization

    Returns:
        Optimized list of polar points
    """
    optimizer = PathOptimizer(rotor_spr, rotor_acceleration, rotor_max_speed,
                             turntable_spr, turntable_acceleration, turntable_max_speed)
    return optimizer.optimize_path(points, algorithm, start_position)


