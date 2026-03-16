"""Robust QR decoding helpers used by the WiFi setup task."""

from __future__ import annotations

from collections import Counter, deque
from typing import Optional

import logging
from PIL import Image
import numpy as np

try:  # pragma: no cover - optional dependency on systems without zxingcpp
    import zxingcpp  # type: ignore
except Exception as exc:  # noqa: BLE001 - optional import
    zxingcpp = None
    ZXING_IMPORT_ERROR = exc
else:
    ZXING_IMPORT_ERROR = None

logger = logging.getLogger(__name__)


class ZxingQRReader:
    """Thin convenience wrapper around :func:`zxingcpp.read_barcodes`."""

    def __init__(self, max_edge: int | None = None, upscale_factor: int = 2, **legacy_flags: object) -> None:
        if zxingcpp is None:  # pragma: no cover - executed only when dependency missing
            logger.error("zxingcpp dependency missing – QR reader cannot start.")
            raise RuntimeError(
                "zxingcpp is not installed. Install the 'zxing-cpp' Python package to enable QR scanning."
            ) from ZXING_IMPORT_ERROR

        self.max_edge = max_edge if (max_edge is None or max_edge > 0) else None
        self.upscale_factor = max(1, upscale_factor)

        if legacy_flags:
            logger.debug("Ignoring legacy QR reader flags: %s", ", ".join(sorted(legacy_flags.keys())))

    def decode(self, frame: np.ndarray) -> Optional[str]:
        """Return the decoded QR text or ``None`` if nothing is found."""
        if frame is None or frame.size == 0:
            return None

        base = self._ensure_uint8(frame)
        if self.max_edge:
            base = self._resize_max_edge(base, self.max_edge)

        variants = self._variants(base)
        for variant in variants:
            try:
                results = zxingcpp.read_barcodes(variant)
            except TypeError as exc:  # pragma: no cover - indicates API drift
                logger.error("zxingcpp.read_barcodes signature mismatch: %s", exc)
                raise
            except Exception as exc:  # noqa: BLE001 - decoder errors should not abort entire scan
                logger.debug("zxingcpp.read_barcodes failed: %s", exc, exc_info=True)
                continue

            for result in results or []:
                text = getattr(result, "text", None)
                if text:
                    logger.info("QR decode succeeded (length %d)", len(text))
                    return text

        #logger.debug("QR decode attempt finished with no matches")
        return None

    def _variants(self, frame: np.ndarray) -> list[np.ndarray]:
        variants: list[np.ndarray] = []

        if frame.ndim == 3:
            variants.append(frame)
            gray = self._to_grayscale(frame)
        else:
            gray = frame

        variants.append(gray)

        stretched = self._stretch_contrast(gray)
        if stretched is not gray:
            variants.append(stretched)

        threshold = self._threshold(gray)
        variants.append(threshold)

        inverted_gray = self._invert(gray)
        variants.append(inverted_gray)
        variants.append(self._invert(threshold))

        if max(gray.shape) < 960 and self.upscale_factor > 1:
            upscaled = self._upscale(gray, factor=self.upscale_factor)
            variants.append(upscaled)
            variants.append(self._invert(upscaled))

        return variants

    def _ensure_uint8(self, frame: np.ndarray) -> np.ndarray:
        array = np.asarray(frame)
        if array.dtype == np.uint8:
            return array

        if np.issubdtype(array.dtype, np.floating):
            scaled = np.clip(array * 255.0, 0, 255)
        else:
            scaled = np.clip(array, 0, 255)
        return scaled.astype(np.uint8)

    def _to_grayscale(self, frame: np.ndarray) -> np.ndarray:
        if frame.ndim != 3 or frame.shape[2] < 3:
            return frame
        # Assume RGB ordering (Picamera2 returns RGB arrays)
        r, g, b = frame[..., 0], frame[..., 1], frame[..., 2]
        gray = (0.299 * r + 0.587 * g + 0.114 * b)
        return np.clip(gray, 0, 255).astype(np.uint8)

    def _stretch_contrast(self, image: np.ndarray) -> np.ndarray:
        # Simple min/max normalization to boost local contrast
        min_val = float(image.min())
        max_val = float(image.max())
        if max_val - min_val < 10:
            return image
        stretched = (image - min_val) * (255.0 / (max_val - min_val))
        return np.clip(stretched, 0, 255).astype(np.uint8)

    def _threshold(self, image: np.ndarray) -> np.ndarray:
        median = np.median(image)
        threshold = np.where(image > median, 255, 0).astype(np.uint8)
        return threshold

    def _invert(self, image: np.ndarray) -> np.ndarray:
        return 255 - image

    def _upscale(self, image: np.ndarray, factor: int = 2) -> np.ndarray:
        return np.repeat(np.repeat(image, factor, axis=0), factor, axis=1)

    def _resize_max_edge(self, image: np.ndarray, max_edge: int) -> np.ndarray:
        height, width = image.shape[:2]
        current_edge = max(height, width)
        if current_edge <= max_edge:
            return image

        scale = max_edge / float(current_edge)
        new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
        pil_image = Image.fromarray(image)
        resized = pil_image.resize(new_size, Image.LANCZOS)
        return np.array(resized)



class StableQRConsensus:
    """Accept a payload only after it was confirmed across multiple frames."""

    def __init__(self, reader: ZxingQRReader, required_hits: int = 3, window: int = 5):
        self.reader = reader
        self.required_hits = required_hits
        self.history: deque[Optional[str]] = deque(maxlen=window)

    def feed(self, frame: np.ndarray) -> Optional[str]:
        text = self.reader.decode(frame)
        self.history.append(text)

        valid = [value for value in self.history if value]
        if not valid:
            return None

        value, count = Counter(valid).most_common(1)[0]
        if count >= self.required_hits:
            return value

        return None
