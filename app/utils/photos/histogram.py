import numpy as np
import cv2


def calculate_histogram(frame):
    """Calculate RGB histogram from frame.

    Args:
        frame: BGR image array

    Returns:
        Dictionary with histogram data
    """
    # TODO: Implement
    return {"r": [], "g": [], "b": []}


def apply_histogram(frame, histogram):
    """Draw histogram overlay on frame.

    Args:
        frame: BGR image array
        histogram: Histogram data from calculate_histogram()

    Returns:
        Frame with histogram overlay
    """
    # TODO: Implement
    return frame