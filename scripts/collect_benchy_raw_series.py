#!/usr/bin/env python3
"""Capture and persist a serial Benchy RGB frame series through the API."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen


DEFAULT_TURNTABLE_OFFSETS = (0.0, 120.0, -120.0)
DEFAULT_FOCUS_VALUES = (10.0, 12.0, 15.0)
SOCKET_HEIGHTS_CM = (3, 5, 7, 10)


class ApiClient:
    def __init__(self, base_url: str, timeout_s: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    def request_json(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = self.url(path, query)
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(
            url,
            data=body,
            method=method,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        with self.open(request) as response:
            return json.loads(response.read().decode("utf-8"))

    def download(self, url: str, path: Path) -> None:
        request = Request(url, headers={"Accept": "application/octet-stream"})
        with self.open(request) as response, path.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)

    def resolve_payload_url(self, payload_url: str) -> str:
        resolved = urljoin(self.base_url + "/", payload_url)
        payload = urlsplit(resolved)
        base = urlsplit(self.base_url)
        base_path = base.path.rstrip("/")
        if payload.netloc == base.netloc and base_path and not payload.path.startswith(base_path + "/"):
            payload = payload._replace(path=base_path + payload.path)
        # Older deployed next routers generated this URL with the v0.9 route
        # name. The payload is stored in the next router's cache, so repair it
        # locally until that firmware is deployed with unique route names.
        if "/v0.9/cameras/" in payload.path:
            payload = payload._replace(path=payload.path.replace("/v0.9/cameras/", "/next/cameras/", 1))
        return urlunsplit(payload)

    def get_camera_names(self) -> list[str]:
        return sorted(self.request_json("GET", "/next/cameras/").keys())

    def get_light_names(self) -> list[str]:
        return sorted(self.request_json("GET", "/next/lights/").keys())

    def set_light(self, light_name: str, on: bool) -> None:
        action = "turn_on" if on else "turn_off"
        self.request_json("PATCH", f"/next/lights/{quote(light_name, safe='')}/{action}")

    def url(self, path: str, query: dict[str, Any] | None = None) -> str:
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        if query:
            url = f"{url}?{urlencode(query)}"
        return url

    def open(self, request: Request):
        try:
            return urlopen(request, timeout=self.timeout_s)
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"{request.get_method()} {request.full_url} returned HTTP {exc.code}: {detail}"
            ) from exc
        except URLError as exc:
            raise RuntimeError(f"Could not reach {self.base_url}: {exc.reason}") from exc


def main() -> None:
    args = parse_args()
    client = ApiClient(args.base_url)
    camera_name = args.camera_name or choose_camera(client)
    args.camera_name = camera_name
    light_name = resolve_light(client, args.light_name)
    original_camera_settings = client.request_json(
        "GET", f"/next/cameras/{quote(camera_name, safe='')}/settings"
    )
    original_motor = client.request_json("GET", "/next/motors/turntable")
    run_name = time.strftime("%Y%m%d-%H%M%S")
    output_dir = args.output_dir / run_name
    output_dir.mkdir(parents=True, exist_ok=True)
    run_manifest: dict[str, Any] = {
        "camera_name": camera_name,
        "light_name": light_name,
        "socket_heights_cm": args.socket_heights_cm,
        "turntable_start_angle": args.start_angle,
        "turntable_offsets": args.turntable_offsets,
        "focus_values": args.focus_values,
        "include_autofocus": not args.no_autofocus,
        "sockets": [],
    }
    manifest_path = output_dir / "series.json"

    try:
        if light_name is not None:
            client.set_light(light_name, False)
        for socket_index, socket_height_cm in enumerate(args.socket_heights_cm):
            if socket_index == 0:
                input(f"Set the {socket_height_cm} cm socket and press ENTER to start...")
            else:
                input(f"Set the {socket_height_cm} cm socket and press ENTER to continue...")

            if light_name is not None:
                client.set_light(light_name, True)
                time.sleep(args.light_cooldown_s)
            socket_dir = output_dir / f"socket_{socket_height_cm:02d}cm"
            socket_dir.mkdir()
            manifest = create_socket_manifest(args, socket_height_cm)
            capture_series(client, args, manifest, socket_dir)
            (socket_dir / "series.json").write_text(
                json.dumps(manifest, indent=2), encoding="utf-8"
            )
            run_manifest["sockets"].append(
                {"socket_height_cm": socket_height_cm, "directory": socket_dir.name}
            )
            manifest_path.write_text(json.dumps(run_manifest, indent=2), encoding="utf-8")
            if light_name is not None:
                client.set_light(light_name, False)
            print(f"Saved {len(manifest['frames'])} frames for the {socket_height_cm} cm socket.")
    finally:
        client.request_json(
            "PUT",
            f"/next/cameras/{quote(camera_name, safe='')}/settings",
            original_camera_settings,
        )
        client.request_json(
            "PUT",
            "/next/motors/turntable/angle",
            query={"degrees": original_motor["angle"]},
        )
        if light_name is not None:
            client.set_light(light_name, False)
        manifest_path.write_text(json.dumps(run_manifest, indent=2), encoding="utf-8")

    print(f"Saved raw socket series to {output_dir}")


def create_socket_manifest(args: argparse.Namespace, socket_height_cm: int) -> dict[str, Any]:
    return {
        "camera_name": args.camera_name,
        "socket_height_cm": socket_height_cm,
        "turntable_start_angle": args.start_angle,
        "turntable_offsets": args.turntable_offsets,
        "focus_values": args.focus_values,
        "include_autofocus": not args.no_autofocus,
        "frames": [],
    }


def capture_series(
    client: ApiClient,
    args: argparse.Namespace,
    manifest: dict[str, Any],
    output_dir: Path,
) -> None:
    capture_plan = []
    if not args.no_autofocus:
        capture_plan.append(("autofocus", None))
    capture_plan.extend(("manual", value) for value in args.focus_values)
    if not capture_plan:
        raise ValueError("Enable autofocus or provide at least one focus value.")

    capture_number = 0
    for position_index, offset in enumerate(args.turntable_offsets):
        angle = (args.start_angle + offset) % 360
        client.request_json(
            "PUT", "/next/motors/turntable/angle", query={"degrees": angle}
        )
        time.sleep(args.settle_time_s)

        for focus_index, (focus_mode, focus) in enumerate(capture_plan):
            settings = {"AF": True} if focus_mode == "autofocus" else {
                "AF": False,
                "manual_focus": focus,
            }
            client.request_json(
                "PATCH",
                f"/next/cameras/{quote(args.camera_name, safe='')}/settings",
                settings,
            )
            time.sleep(args.focus_settle_time_s)

            metadata = client.request_json(
                "GET",
                f"/next/cameras/{quote(args.camera_name, safe='')}/photo",
                query={"image_format": "rgb_array", "with_metadata": "true"},
            )
            filename = f"frame_{position_index:02d}_{focus_index:02d}_{focus_mode}"
            array_path = output_dir / f"{filename}.npy"
            metadata_path = output_dir / f"{filename}.json"
            metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
            client.download(client.resolve_payload_url(metadata["payload_url"]), array_path)
            manifest["frames"].append(
                {
                    "index": capture_number,
                    "position_index": position_index,
                    "focus_index": focus_index,
                    "turntable_offset": offset,
                    "turntable_angle": angle,
                    "focus_mode": focus_mode,
                    "requested_focus": focus,
                    "array_file": array_path.name,
                    "metadata_file": metadata_path.name,
                    "format": metadata["format"],
                    "camera_metadata": metadata["camera_metadata"],
                }
            )
            manifest_path = output_dir / "series.json"
            manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            capture_number += 1
            print(f"Captured {filename}")


def choose_camera(client: ApiClient) -> str:
    cameras = client.get_camera_names()
    if not cameras:
        raise RuntimeError("The API reported no cameras.")
    if len(cameras) == 1:
        return cameras[0]
    print(f"Available cameras: {', '.join(cameras)}")
    while True:
        selected = input("Camera: ").strip()
        if selected in cameras:
            return selected


def resolve_light(client: ApiClient, requested_name: str | None) -> str | None:
    if requested_name is not None:
        return requested_name
    names = client.get_light_names()
    if "ring" in names:
        return "ring"
    if len(names) == 1:
        return names[0]
    if len(names) > 1:
        print(f"Available lights: {', '.join(names)}")
        selected = input("Light name (empty for none): ").strip()
        return selected or None
    return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://openscan3-imx519/api")
    parser.add_argument("--camera-name")
    parser.add_argument(
        "--socket-height-cm",
        type=int,
        choices=SOCKET_HEIGHTS_CM,
        help="Capture only this socket; otherwise capture 3, 5, 7, and 10 cm.",
    )
    parser.add_argument("--light-name", help="Light name; otherwise discover it through the API.")
    parser.add_argument("--output-dir", type=Path, default=Path("data/benchy-raw-series"))
    parser.add_argument("--start-angle", type=float, default=90.0)
    parser.add_argument("--turntable-offsets", type=float, nargs="+", default=list(DEFAULT_TURNTABLE_OFFSETS))
    parser.add_argument("--settle-time-s", type=float, default=0.25)
    parser.add_argument("--focus-settle-time-s", type=float, default=0.2)
    parser.add_argument(
        "--light-cooldown-s",
        type=float,
        default=2.0,
        help="Wait after switching on the light before the first capture.",
    )
    parser.add_argument("--focus-values", type=float, nargs="+", default=list(DEFAULT_FOCUS_VALUES))
    parser.add_argument("--no-autofocus", action="store_true")
    args = parser.parse_args()
    args.socket_heights_cm = (
        [args.socket_height_cm]
        if args.socket_height_cm is not None
        else list(SOCKET_HEIGHTS_CM)
    )
    return args


if __name__ == "__main__":
    main()
