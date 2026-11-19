#!/usr/bin/env python3
"""Analyze stored Benchy RGB arrays without contacting the scanner."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from openscan_firmware.models.camera import CameraMetadata
from openscan_firmware.models.paths import PolarPoint3D
from openscan_firmware.utils.photos.analysis import (
    AnalysisFrame,
    _combine_roi_masks,
    _crop_recommendation,
    _detect_roi_mask,
    _mask_report,
    _roi_report,
    _trim_narrow_tail,
    apply_orientation,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=Path("data/benchy-raw-series"))
    parser.add_argument("--run", help="Run directory name; defaults to newest complete run.")
    parser.add_argument("--output-dir", type=Path, default=Path("debug/benchy-raw-series"))
    parser.add_argument("--max-dimension", type=int, default=640)
    parser.add_argument("--tail-width-ratio", type=float, default=0.4)
    parser.add_argument("--tail-min-rows", type=int, default=12)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_dir = choose_run(args.input_dir, args.run)
    output_dir = args.output_dir / run_dir.name
    output_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {"source_run": str(run_dir), "sockets": []}

    for socket_dir in sorted(run_dir.glob("socket_*cm")):
        socket_result = analyze_socket(
            socket_dir,
            output_dir,
            args.max_dimension,
            args.tail_width_ratio,
            args.tail_min_rows,
        )
        result["sockets"].append(socket_result)

    summary_path = output_dir / "summary.json"
    summary_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"Analyzed {run_dir}")
    print(f"Wrote {summary_path}")


def choose_run(input_dir: Path, requested_run: str | None) -> Path:
    if requested_run:
        run_dir = input_dir / requested_run
        if not is_complete_run(run_dir):
            raise SystemExit(f"Run is missing one or more socket series: {run_dir}")
        return run_dir

    runs = [path for path in input_dir.iterdir() if path.is_dir() and is_complete_run(path)]
    if not runs:
        raise SystemExit(f"No complete raw series found in {input_dir}.")
    return sorted(runs)[-1]


def is_complete_run(run_dir: Path) -> bool:
    manifest_path = run_dir / "series.json"
    if not manifest_path.exists():
        return False
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    expected = set(manifest.get("socket_heights_cm", []))
    actual = {
        int(path.name.removeprefix("socket_").removesuffix("cm"))
        for path in run_dir.glob("socket_*cm")
    }
    return expected == actual and bool(expected)


def analyze_socket(
    socket_dir: Path,
    output_dir: Path,
    max_dimension: int,
    tail_width_ratio: float,
    tail_min_rows: int,
) -> dict[str, Any]:
    manifest = json.loads((socket_dir / "series.json").read_text(encoding="utf-8"))
    socket_height = manifest["socket_height_cm"]
    groups: dict[str, list[dict[str, Any]]] = {}
    for frame in manifest["frames"]:
        focus = "autofocus" if frame["focus_mode"] == "autofocus" else f"{frame['requested_focus']:g}"
        groups.setdefault(focus, []).append(frame)

    detections = {}
    for focus, frames in groups.items():
        analysis_frames = [load_analysis_frame(socket_dir, frame, max_dimension) for frame in frames]
        detections[focus] = _detect_roi_mask(analysis_frames)

    focus_reports = {
        focus: _roi_report(roi_mask)
        for focus, roi_mask in detections.items()
    }
    consensus_mask = _combine_roi_masks(detections)
    object_mask, mask_cleanup = _trim_narrow_tail(
        consensus_mask,
        width_ratio=tail_width_ratio,
        min_rows=tail_min_rows,
    )
    consensus_report = _mask_report(object_mask)
    consensus_report.update(
        {
            "focus_series": list(detections),
            "mask_cleanup": mask_cleanup,
            "crop_recommendation": _crop_recommendation(object_mask),
        }
    )

    socket_result: dict[str, Any] = {
        "socket_height_cm": socket_height,
        "focus_series": focus_reports,
        "consensus": consensus_report,
    }

    for focus, roi_mask in detections.items():
        mask_path = output_dir / f"socket_{socket_height:02d}cm_focus_{focus}_roi_mask.png"
        Image.fromarray(roi_mask.mask.astype(np.uint8) * 255, mode="L").save(mask_path)
    Image.fromarray(consensus_mask.astype(np.uint8) * 255, mode="L").save(
        output_dir / f"socket_{socket_height:02d}cm_focus_consensus_mask.png"
    )
    Image.fromarray(object_mask.astype(np.uint8) * 255, mode="L").save(
        output_dir / f"socket_{socket_height:02d}cm_object_mask.png"
    )
    return socket_result


def load_analysis_frame(
    socket_dir: Path,
    frame: dict[str, Any],
    max_dimension: int,
) -> AnalysisFrame:
    raw = np.load(socket_dir / frame["array_file"], mmap_mode="r")
    metadata = json.loads((socket_dir / frame["metadata_file"]).read_text(encoding="utf-8"))
    camera_metadata = CameraMetadata.model_validate(metadata["camera_metadata"])
    orientation_flag = camera_metadata.camera_settings.orientation_flag or 1
    oriented = apply_orientation(raw, orientation_flag)
    height, width = oriented.shape[:2]
    scale = min(1.0, max_dimension / max(height, width))
    if scale == 1.0:
        analysis_image = oriented.copy()
    else:
        size = (max(1, round(width * scale)), max(1, round(height * scale)))
        analysis_image = np.asarray(
            Image.fromarray(oriented).resize(size, Image.Resampling.BILINEAR)
        )

    return AnalysisFrame(
        index=frame["index"],
        position=PolarPoint3D(theta=90.0, fi=frame["turntable_angle"], r=1.0),
        photo_format="rgb_array",
        camera_metadata=camera_metadata,
        analysis_image=analysis_image,
    )


if __name__ == "__main__":
    main()
