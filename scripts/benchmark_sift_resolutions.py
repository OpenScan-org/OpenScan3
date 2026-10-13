#!/usr/bin/env python3
"""Benchmark SIFT on two saved RGB frames at several image resolutions."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from openscan_firmware.utils.photos.analysis import analyze_sift_pair
from openscan_firmware.utils.photos.analysis import crop_centered_image


# Diagnostic resolutions, ordered from the fast preview check to the detailed
# fallback. Full resolution is intentionally excluded because it is too costly
# on the Raspberry Pi.
DEFAULT_MAX_DIMENSION = 960
DEFAULT_MAX_DIMENSIONS: tuple[int, ...] = (640, DEFAULT_MAX_DIMENSION, 1280)


def _resize_to_max_dimension(image: np.ndarray, max_dimension: int | None) -> np.ndarray:
    """Resize an image while preserving its aspect ratio."""
    if max_dimension is None:
        return image
    height, width = image.shape[:2]
    current_max = max(height, width)
    if current_max <= max_dimension:
        return image
    scale = max_dimension / current_max
    size = (max(1, round(width * scale)), max(1, round(height * scale)))
    return cv2.resize(image, size, interpolation=cv2.INTER_AREA)


def benchmark(
    first_path: Path,
    second_path: Path,
    *,
    crop_width: float = 0.0,
    crop_height: float = 0.0,
) -> list[dict[str, Any]]:
    """Run SIFT on the saved image pair at all benchmark resolutions."""
    first_image = np.load(first_path, allow_pickle=False)
    second_image = np.load(second_path, allow_pickle=False)
    if first_image.ndim != 3 or second_image.ndim != 3:
        raise ValueError("Benchmark images must be RGB arrays.")

    results = []
    for max_dimension in DEFAULT_MAX_DIMENSIONS:
        resized_first = crop_centered_image(
            _resize_to_max_dimension(first_image, max_dimension),
            crop_width=crop_width,
            crop_height=crop_height,
        )
        resized_second = crop_centered_image(
            _resize_to_max_dimension(second_image, max_dimension),
            crop_width=crop_width,
            crop_height=crop_height,
        )
        started = time.perf_counter()
        metrics = analyze_sift_pair(resized_first, resized_second)
        elapsed_ms = (time.perf_counter() - started) * 1000
        result = {
            "max_dimension": max_dimension or "full",
            "first_shape": list(resized_first.shape),
            "second_shape": list(resized_second.shape),
            "elapsed_ms": round(elapsed_ms, 3),
            **metrics,
        }
        results.append(result)
        print(f"SIFT_RESOLUTION {json.dumps(result, separators=(',', ':'))}", flush=True)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("first_image", type=Path)
    parser.add_argument("second_image", type=Path)
    parser.add_argument("--crop-width", type=float, default=0.0)
    parser.add_argument("--crop-height", type=float, default=0.0)
    args = parser.parse_args()
    started = time.perf_counter()
    results = benchmark(
        args.first_image,
        args.second_image,
        crop_width=args.crop_width,
        crop_height=args.crop_height,
    )
    print(
        f"SIFT_BENCHMARK completed_levels={len(results)} "
        f"total_elapsed_ms={(time.perf_counter() - started) * 1000:.3f}",
        flush=True,
    )


if __name__ == "__main__":
    main()
