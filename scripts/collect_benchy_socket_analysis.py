#!/usr/bin/env python3
"""Collect scan-analysis reports for the Benchy on all turntable sockets."""
from __future__ import annotations

import argparse
import base64
import json
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


SOCKET_HEIGHTS_CM = (3, 5, 7, 10)
TERMINAL_STATUSES = {"completed", "error", "cancelled", "interrupted"}


class ApiClient:
    def __init__(self, base_url: str, timeout_s: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(
            f"{self.base_url}{path}",
            data=data,
            method=method,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=self.timeout_s) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"{method} {path} returned HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise RuntimeError(f"Could not reach {self.base_url}: {exc.reason}") from exc

    def start_analysis(
        self,
        camera_name: str,
        focus_values: list[float],
        include_autofocus: bool,
        debug_images: bool,
    ) -> dict[str, Any]:
        return self.request(
            "POST",
            "/next/scan-analysis",
            {
                "camera_name": camera_name,
                "turntable_offsets": [0.0, 120.0, -120.0],
                "settle_time_s": 0.25,
                "focus_values": focus_values,
                "include_autofocus": include_autofocus,
                "debug_images": debug_images,
            },
        )

    def get_task(self, task_id: str) -> dict[str, Any]:
        return self.request("GET", f"/next/tasks/{task_id}")

    def get_camera_names(self) -> list[str]:
        return sorted(self.request("GET", "/next/cameras/").keys())

    def get_light_names(self) -> list[str]:
        return sorted(self.request("GET", "/next/lights/").keys())

    def set_light(self, light_name: str, on: bool) -> None:
        action = "turn_on" if on else "turn_off"
        encoded_name = quote(light_name, safe="")
        self.request("PATCH", f"/next/lights/{encoded_name}/{action}")


def wait_for_task(client: ApiClient, task_id: str, timeout_s: float, poll_s: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        task = client.get_task(task_id)
        status = task.get("status")
        print(f"  task {task_id}: {status}")
        if status in TERMINAL_STATUSES:
            return task
        time.sleep(poll_s)
    raise TimeoutError(f"Task {task_id} did not finish within {timeout_s:.0f} seconds.")


def wait_for_socket_change(client: ApiClient, light_name: str | None, message: str) -> None:
    if light_name is not None:
        client.set_light(light_name, False)
    input(message)


def save_task(output_dir: Path, socket_height_cm: int, task: dict[str, Any]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    task_id = task.get("id", "unknown-task")
    output_path = output_dir / f"benchy_socket_{socket_height_cm:02d}cm_{task_id}.json"
    output_path.write_text(json.dumps(task, indent=2), encoding="utf-8")
    save_debug_images(output_dir, socket_height_cm, task)
    return output_path


def save_debug_images(output_dir: Path, socket_height_cm: int, task: dict[str, Any]) -> None:
    """Write optional task debug images next to the JSON report."""
    debug_images = task.get("result", {}).get("debug_images", {})
    prefix = f"benchy_socket_{socket_height_cm:02d}cm"
    for frame in debug_images.get("captured_frames", []):
        focus = frame["focus_mode"]
        value = frame.get("requested_focus")
        label = focus if value is None else f"{focus}_{value:g}"
        path = output_dir / (
            f"{prefix}_frame_{frame['index']:02d}_{frame['capture_index']:02d}_{label}.jpg"
        )
        path.write_bytes(base64.b64decode(frame["image_base64"]))

    for focus, images in debug_images.get("roi_masks", {}).items():
        for name, encoded in images.items():
            path = output_dir / f"{prefix}_roi_{focus}_{name.removesuffix('_base64')}.jpg"
            path.write_bytes(base64.b64decode(encoded))

    for name in ("consensus_mask_base64", "object_mask_base64"):
        encoded = debug_images.get(name)
        if encoded:
            path = output_dir / f"{prefix}_{name.removesuffix('_base64')}.jpg"
            path.write_bytes(base64.b64decode(encoded))


def choose_name(kind: str, names: list[str]) -> str:
    if not names:
        raise RuntimeError(f"The API did not report any {kind}.")
    if len(names) == 1:
        return names[0]

    print(f"Available {kind}: {', '.join(names)}")
    while True:
        selected = input(f"Select {kind}: ").strip()
        if selected in names:
            return selected
        print(f"Unknown {kind} '{selected}'. Choose one of: {', '.join(names)}")


def resolve_camera_name(client: ApiClient, requested_name: str | None) -> str:
    if requested_name is not None:
        return requested_name
    return choose_name("camera", client.get_camera_names())


def resolve_light_name(client: ApiClient, requested_name: str | None) -> str | None:
    if requested_name is not None:
        return requested_name

    names = client.get_light_names()
    if "ring" in names:
        return "ring"
    if len(names) == 1:
        return names[0]
    if not names:
        print("The API reported no lights; continuing without ringlight feedback.")
        return None
    return choose_name("light", names)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://openscan3-imx519/api")
    parser.add_argument("--camera-name", help="Camera name; otherwise discover it through the API.")
    parser.add_argument("--light-name", help="Light name; otherwise discover it through the API.")
    parser.add_argument("--output-dir", type=Path, default=Path("data/benchy-socket-analysis"))
    parser.add_argument("--task-timeout", type=float, default=180.0)
    parser.add_argument("--poll-interval", type=float, default=1.0)
    parser.add_argument(
        "--focus-values",
        type=float,
        nargs="+",
        default=[10.0, 12.0, 15.0],
        help="Fixed manual focus values to capture at each position.",
    )
    parser.add_argument(
        "--no-autofocus",
        action="store_true",
        help="Skip the autofocus capture at each position.",
    )
    parser.add_argument(
        "--no-debug-images",
        action="store_true",
        help="Do not return or save small debug images.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    client = ApiClient(args.base_url)
    camera_name = resolve_camera_name(client, args.camera_name)
    light_name = resolve_light_name(client, args.light_name)

    print(f"Collecting Benchy analysis from {args.base_url}")
    print(f"Using camera: {camera_name}")
    print(f"Using light: {light_name or 'none'}")
    focus_description = ", ".join(f"{value:g}" for value in args.focus_values)
    print(f"Focus series: {'autofocus, ' if not args.no_autofocus else ''}{focus_description}")
    print(f"Results will be written to {args.output_dir}")

    for index, socket_height_cm in enumerate(SOCKET_HEIGHTS_CM):
        if index == 0:
            input(f"Set the {socket_height_cm} cm socket and press ENTER to start...")
        else:
            wait_for_socket_change(
                client,
                light_name,
                f"\nSet the {socket_height_cm} cm socket and press ENTER to continue...",
            )

        print(f"Starting analysis for {socket_height_cm} cm socket.")
        if light_name is not None:
            client.set_light(light_name, True)
        task = client.start_analysis(
            camera_name,
            focus_values=args.focus_values,
            include_autofocus=not args.no_autofocus,
            debug_images=not args.no_debug_images,
        )
        task_id = task["id"]
        print(f"Started task {task_id}.")

        final_task = wait_for_task(client, task_id, args.task_timeout, args.poll_interval)
        output_path = save_task(args.output_dir, socket_height_cm, final_task)
        print(f"Saved result to {output_path}")

        if final_task.get("status") != "completed":
            if light_name is not None:
                client.set_light(light_name, False)
            raise RuntimeError(
                f"Analysis for {socket_height_cm} cm socket ended with "
                f"status {final_task.get('status')}: {final_task.get('error')}"
            )

    if light_name is not None:
        client.set_light(light_name, False)
    print("Finished all socket measurements.")


if __name__ == "__main__":
    main()
