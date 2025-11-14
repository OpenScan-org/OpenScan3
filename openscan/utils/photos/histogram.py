import numpy as np
import cv2


def calculate_histogram(frame):
    """Calculate RGB histogram from frame.

    Args:
        frame: BGR image array

    Returns:
        Dictionary with histogram data (256 bins per channel)
    """
    # Split BGR channels
    b, g, r = cv2.split(frame)

    # Calculate histograms (256 bins from 0-255)
    hist_r = cv2.calcHist([r], [0], None, [256], [0, 256]).flatten()
    hist_g = cv2.calcHist([g], [0], None, [256], [0, 256]).flatten()
    hist_b = cv2.calcHist([b], [0], None, [256], [0, 256]).flatten()

    return {
        "r": hist_r,
        "g": hist_g,
        "b": hist_b
    }


def apply_histogram(frame, histogram, position='bottom-right', size=(250, 125)):
    """Draw histogram overlay on frame.

    Args:
        frame: BGR image array
        histogram: Histogram data from calculate_histogram()
        position: 'bottom-right', 'bottom-left', 'top-right', 'top-left'
        size: (width, height) of histogram

    Returns:
        Histogram overlay with BGRA format
    """
    h, w = frame.shape[:2]
    hist_w, hist_h = size

    # Create overlay
    overlay = np.zeros((h, w, 4), dtype=np.uint8)

    # Calculate position
    margin = 12
    if position == 'bottom-right':
        x, y = w - hist_w - margin, h - hist_h - margin
    elif position == 'bottom-left':
        x, y = margin, h - hist_h - margin
    elif position == 'top-right':
        x, y = w - hist_w - margin, margin
    else:  # top-left
        x, y = margin, margin

    # Draw dark background
    overlay[y:y + hist_h, x:x + hist_w] = (0, 0, 0, 179)  # ~0.7 alpha

    # Find max value for scaling (log scale)
    max_val = max(histogram['r'].max(), histogram['g'].max(), histogram['b'].max())
    max_log = np.log10(max_val + 1)

    # Draw each channel
    for i, (color_bgr, hist_data) in enumerate([
        ((0, 0, 255), histogram['r']),  # Red
        ((0, 255, 0), histogram['g']),  # Green
        ((255, 0, 0), histogram['b'])  # Blue
    ]):
        points = []
        for bin_idx in range(256):
            px = x + int((bin_idx / 256) * hist_w)
            log_val = np.log10(hist_data[bin_idx] + 1)
            normalized = log_val / max_log if max_log > 0 else 0
            py = y + hist_h - int(normalized * hist_h)
            points.append([px, py])

        points = np.array(points, dtype=np.int32)
        cv2.polylines(overlay, [points], False, (*color_bgr, 179), 1, cv2.LINE_AA)

    # Add border
    cv2.rectangle(overlay, (x, y), (x + hist_w, y + hist_h), (255, 255, 255, 76), 1)

    return overlay