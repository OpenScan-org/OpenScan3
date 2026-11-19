"""Helpers for turning Raspberry Pi ISP statistics into safe debug data.

The Raspberry Pi camera stack exposes the ISP statistics as a vendor specific
binary metadata blob.  Keeping the decoder here means the rest of the camera
controller and the scan analysis task can work with a stable, JSON compatible
shape while we add support for more statistics later.
"""
from __future__ import annotations

import logging
import struct
from typing import Any, Mapping

logger = logging.getLogger(__name__)


# bcm2835_isp_stats from linux/bcm2835-isp.h on the Pi 4/VC4.  The structure
# uses native 64 bit alignment, which results in these little endian offsets
# on the Raspberry Pi's arm64 userspace.
_BCM_FOCUS_OFFSET = 10_248
_BCM_FOCUS_REGION_SIZE = 48
_BCM_FOCUS_REGION_COUNT = 12
_BCM_FOCUS_GRID_ROWS = 3
_BCM_FOCUS_GRID_COLUMNS = 4


def decode_extended_debug_metadata(metadata: Mapping[str, Any]) -> dict[str, Any] | None:
    """Decode supported vendor statistics without retaining binary blobs.

    Unknown vendor blobs are reported as available but unsupported.  A camera
    capture must remain usable when a kernel or libcamera release changes the
    metadata layout, so parser errors are returned as debug information rather
    than raised to the caller.
    """
    vendor_keys = ("Bcm2835StatsOutput", "PispStatsOutput")
    present_keys = [key for key in vendor_keys if key in metadata]
    stats_enabled = metadata.get("StatsOutputEnable")
    if not present_keys and stats_enabled is None:
        return None

    result: dict[str, Any] = {
        "schema_version": 1,
        "stats_output_enabled": bool(stats_enabled) if stats_enabled is not None else None,
        "sources": [],
        "errors": [],
    }

    for key in present_keys:
        value = metadata[key]
        source: dict[str, Any] = {
            "metadata_key": key,
            "blob_size": _blob_size(value),
            "parser_supported": key == "Bcm2835StatsOutput",
        }
        if key == "Bcm2835StatsOutput":
            try:
                source["version"], source["declared_size"], focus = _decode_bcm2835_focus(value)
                source["focus"] = focus
            except (TypeError, ValueError, struct.error) as exc:
                source["parser_supported"] = False
                source["error"] = str(exc)
                result["errors"].append(f"{key}: {exc}")
                logger.warning("Could not decode %s: %s", key, exc)
        result["sources"].append(source)

    focus_sources = [source["focus"] for source in result["sources"] if "focus" in source]
    if focus_sources:
        result["focus"] = focus_sources[0]
    return result


def _decode_bcm2835_focus(value: Any) -> tuple[int, int, dict[str, Any]]:
    """Decode the 4 by 3 VC4 focus statistics used by libcamera on Pi 4."""
    blob = _as_bytes(value)
    required_size = _BCM_FOCUS_OFFSET + _BCM_FOCUS_REGION_COUNT * _BCM_FOCUS_REGION_SIZE
    if len(blob) < required_size:
        raise ValueError(
            f"statistics blob is too short ({len(blob)} bytes, need {required_size})"
        )

    version, declared_size = struct.unpack_from("<II", blob, 0)
    regions: list[dict[str, Any]] = []
    for index in range(_BCM_FOCUS_REGION_COUNT):
        offset = _BCM_FOCUS_OFFSET + index * _BCM_FOCUS_REGION_SIZE
        contrast_values = struct.unpack_from("<4Q", blob, offset)
        contrast_counts = struct.unpack_from("<4I", blob, offset + 32)
        row, column = divmod(index, _BCM_FOCUS_GRID_COLUMNS)
        raw_value = int(contrast_values[3])
        regions.append(
            {
                "index": index,
                "row": row,
                "column": column,
                "rect_normalized": [
                    round(column / _BCM_FOCUS_GRID_COLUMNS, 6),
                    round(row / _BCM_FOCUS_GRID_ROWS, 6),
                    round(1 / _BCM_FOCUS_GRID_COLUMNS, 6),
                    round(1 / _BCM_FOCUS_GRID_ROWS, 6),
                ],
                "focus_value": raw_value / 1000.0,
                "focus_value_raw": raw_value,
                "contrast_value_num": int(contrast_counts[3]),
            }
        )

    return version, declared_size, {
        "source": "bcm2835_isp",
        "coordinate_system": "sensor_normalized",
        "grid_size": {
            "rows": _BCM_FOCUS_GRID_ROWS,
            "columns": _BCM_FOCUS_GRID_COLUMNS,
        },
        "regions": regions,
    }


def _as_bytes(value: Any) -> bytes:
    """Convert Picamera2/libcamera byte-like values to bytes."""
    if isinstance(value, bytes):
        return value
    if isinstance(value, (bytearray, memoryview)):
        return bytes(value)
    try:
        return bytes(value)
    except (TypeError, ValueError) as exc:
        raise TypeError(f"unsupported statistics value type: {type(value).__name__}") from exc


def _blob_size(value: Any) -> int | None:
    try:
        return len(value)
    except TypeError:
        try:
            return len(_as_bytes(value))
        except (TypeError, ValueError):
            return None
