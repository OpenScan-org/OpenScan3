#!/usr/bin/env python3
"""Experiment with robust crop proposals from a stored Benchy raw series."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analyze_benchy_raw_series import (  # noqa: E402
    choose_run,
    load_analysis_frame,
)
from openscan_firmware.utils.photos.analysis import (  # noqa: E402
    _combine_roi_masks,
    _crop_recommendation,
    _detect_roi_mask,
    _roi_report,
    _trim_narrow_tail,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=Path("data/benchy-raw-series"))
    parser.add_argument("--run", help="Run directory name; defaults to newest complete run.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("debug/benchy-raw-series-roi/openscan-crop-experiment"),
    )
    parser.add_argument("--max-dimension", type=int, default=640)
    parser.add_argument("--margin-ratio", type=float, default=0.0)
    parser.add_argument("--tail-width-ratio", type=float, default=0.25)
    parser.add_argument("--tail-min-rows", type=int, default=12)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.margin_ratio < 0 or args.margin_ratio > 0.25:
        raise SystemExit("--margin-ratio must be between 0 and 0.25")
    if args.tail_width_ratio <= 0 or args.tail_width_ratio >= 1:
        raise SystemExit("--tail-width-ratio must be between 0 and 1")
    if args.tail_min_rows < 1:
        raise SystemExit("--tail-min-rows must be positive")

    run_dir = choose_run(args.input_dir, args.run)
    output_dir = args.output_dir / run_dir.name
    output_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {
        "source_run": str(run_dir),
        "margin_ratio": args.margin_ratio,
        "tail_width_ratio": args.tail_width_ratio,
        "tail_min_rows": args.tail_min_rows,
        "sockets": [],
    }

    for socket_dir in sorted(run_dir.glob("socket_*cm")):
        result["sockets"].append(analyze_socket(socket_dir, output_dir, args))

    summary_path = output_dir / "summary.json"
    summary_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"Analyzed {run_dir}")
    print(f"Wrote {summary_path}")


def analyze_socket(socket_dir: Path, output_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    manifest = json.loads((socket_dir / "series.json").read_text(encoding="utf-8"))
    socket_height = manifest["socket_height_cm"]
    groups: dict[str, list[dict[str, Any]]] = {}
    for frame in manifest["frames"]:
        focus = "autofocus" if frame["focus_mode"] == "autofocus" else f"{frame['requested_focus']:g}"
        groups.setdefault(focus, []).append(frame)

    detections = {}
    representative = None
    for focus, frames in groups.items():
        analysis_frames = [load_analysis_frame(socket_dir, frame, args.max_dimension) for frame in frames]
        detections[focus] = _detect_roi_mask(analysis_frames)
        if focus == "12" or representative is None:
            representative = analysis_frames[0].analysis_image

    reports = {focus: _roi_report(detection) for focus, detection in detections.items()}
    consensus = _combine_roi_masks(detections)
    object_mask, tail_trim = _trim_narrow_tail(
        consensus,
        width_ratio=args.tail_width_ratio,
        min_rows=args.tail_min_rows,
    )
    crop = _crop_recommendation(object_mask, args.margin_ratio)
    bbox_diagnostic = robust_bbox_diagnostic(reports, representative.shape[:2])

    prefix = f"socket_{socket_height:02d}cm"
    Image.fromarray(consensus.astype(np.uint8) * 255, mode="L").save(
        output_dir / f"{prefix}_focus_consensus_mask.png"
    )
    Image.fromarray(object_mask.astype(np.uint8) * 255, mode="L").save(
        output_dir / f"{prefix}_object_mask_experiment.png"
    )
    save_overlay(
        output_dir / f"{prefix}_crop_overlay.jpg",
        representative,
        object_mask,
        crop["bbox"],
        crop["rect"],
    )

    return {
        "socket_height_cm": socket_height,
        "focus_series": reports,
        "crop_from_consensus_mask": crop,
        "tail_trim": tail_trim,
        "bbox_diagnostic": bbox_diagnostic,
        "consensus_mask_area_ratio": round(float(consensus.mean()), 6),
        "object_mask_area_ratio": round(float(object_mask.mean()), 6),
    }


def robust_bbox_diagnostic(
    reports: dict[str, dict[str, Any]],
    image_shape: tuple[int, int],
) -> dict[str, Any]:
    height, width = image_shape
    entries = [
        (focus, report)
        for focus, report in reports.items()
        if report["roi_bbox"] is not None
    ]
    if not entries:
        return {"used_focus": [], "outlier_focus": [], "bbox": None}

    boxes = np.array(
        [
            [x, y, x + box_width, y + box_height]
            for _, report in entries
            for x, y, box_width, box_height in [report["roi_bbox"]]
        ],
        dtype=float,
    )
    median = np.median(boxes, axis=0)
    deviations = np.abs(boxes - median)
    minimum_tolerance = np.array([width, height, width, height], dtype=float) * 0.05
    tolerance = np.maximum(np.median(deviations, axis=0) * 3, minimum_tolerance)
    outlier_rows = np.any(deviations > tolerance, axis=1)
    used_boxes = boxes[~outlier_rows]
    used_focus = [focus for (focus, _), outlier in zip(entries, outlier_rows) if not outlier]
    outlier_focus = [focus for (focus, _), outlier in zip(entries, outlier_rows) if outlier]
    robust_bbox = np.median(used_boxes, axis=0)

    return {
        "used_focus": used_focus,
        "outlier_focus": outlier_focus,
        "bbox": [round(float(value), 2) for value in robust_bbox],
    }


def save_overlay(
    path: Path,
    image: np.ndarray,
    mask: np.ndarray,
    bbox: list[int] | None,
    rect: list[int] | None,
) -> None:
    output = Image.fromarray(image).convert("RGB")
    mask_overlay = Image.new("RGBA", output.size, (40, 120, 255, 0))
    mask_overlay.putalpha(Image.fromarray(mask.astype(np.uint8) * 90, mode="L"))
    output = Image.alpha_composite(output.convert("RGBA"), mask_overlay).convert("RGB")
    draw = ImageDraw.Draw(output)
    if bbox is not None:
        x, y, width, height = bbox
        draw.rectangle((x, y, x + width, y + height), outline=(255, 220, 40), width=2)
    if rect is not None:
        x, y, width, height = rect
        draw.rectangle((x, y, x + width, y + height), outline=(255, 40, 40), width=3)
    output.save(path, quality=90)


if __name__ == "__main__":
    main()
