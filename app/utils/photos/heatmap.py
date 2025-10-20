import numpy as np
import cv2
import logging

logger = logging.getLogger(__name__)


def calculate_heatmap(frame, grid_size=20):
    """Calculate heatmap overlay based on variance (cached).

    Args:
        frame: BGR image array
        grid_size: Grid dimensions (NxN)

    Returns:
        Normalized heatmap values (grid_size x grid_size)
    """
    h, w = frame.shape[:2]
    cell_w = w // grid_size
    cell_h = h // grid_size

    # Convert to grayscale for faster processing
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    sizes = np.zeros((grid_size, grid_size))

    for y in range(grid_size):
        for x in range(grid_size):
            x1, y1 = x * cell_w, y * cell_h
            x2, y2 = min((x + 1) * cell_w, w), min((y + 1) * cell_h, h)

            cell = gray[y1:y2, x1:x2]
            sizes[y, x] = np.std(cell)

    # Log statistics
    total_size = sizes.sum()
    min_size = sizes.min()
    max_size = sizes.max()
    mean_size = sizes.mean()

    logger.info(
        f"Heatmap stats - Total: {total_size:.2f}, Min: {min_size:.2f}, Max: {max_size:.2f}, Mean: {mean_size:.2f}")

    # Normalize
    normalized = (sizes - min_size) / (max_size - min_size) if max_size > min_size else sizes

    return normalized


def apply_heatmap(frame, normalized):
    """Apply pre-calculated heatmap to frame.

    Args:
        frame: BGR image array
        normalized: Normalized heatmap values from calculate_heatmap()

    Returns:
        Frame with heatmap overlay
    """
    h, w = frame.shape[:2]
    grid_size = normalized.shape[0]
    cell_w = w // grid_size
    cell_h = h // grid_size

    overlay = np.zeros((h, w, 4), dtype=np.uint8)

    for y in range(grid_size):
        for x in range(grid_size):
            val = normalized[y, x]
            r = int(255 * val)
            b = int(255 * (1 - val))

            x1, y1 = x * cell_w, y * cell_h
            x2, y2 = min((x + 1) * cell_w, w), min((y + 1) * cell_h, h)
            overlay[y1:y2, x1:x2] = (b, 0, r, 153)

    alpha = overlay[:, :, 3:4] / 255.0
    frame = (frame * (1 - alpha) + overlay[:, :, :3] * alpha).astype(np.uint8)

    return frame