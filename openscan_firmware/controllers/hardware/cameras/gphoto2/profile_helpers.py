"""Reusable helpers for GPhoto2 profile implementations."""

from __future__ import annotations

import time
from fractions import Fraction
from typing import Any


def format_shutter_value_ms(shutter_ms: float) -> str:
    seconds = max(shutter_ms / 1000.0, 0.000125)
    if seconds >= 1.0:
        return f"{seconds:.1f}".rstrip("0").rstrip(".")
    reciprocal = round(1.0 / seconds)
    return f"1/{max(reciprocal, 1)}"


def parse_shutter_choice_seconds(value: str) -> float | None:
    normalized = value.strip().lower()
    if not normalized or normalized == "bulb":
        return None
    if "/" in normalized:
        try:
            return float(Fraction(normalized))
        except Exception:
            return None
    try:
        return float(normalized)
    except Exception:
        return None


def select_best_shutter_choice(shutter_ms: float, available_choices: list[Any]) -> str:
    target_seconds = max(shutter_ms / 1000.0, 0.000125)
    if not available_choices:
        return format_shutter_value_ms(shutter_ms)

    best_choice: str | None = None
    best_error = float("inf")
    for choice in available_choices:
        parsed_seconds = parse_shutter_choice_seconds(str(choice))
        if parsed_seconds is None:
            continue
        error = abs(parsed_seconds - target_seconds)
        if error < best_error:
            best_error = error
            best_choice = str(choice)
    return best_choice or format_shutter_value_ms(shutter_ms)


def map_gain_to_iso_choice(gain: float | None, iso_choices: list[int]) -> str | None:
    if gain is None:
        return None
    target = max(float(gain), 0.0) * 100.0
    nearest = min(iso_choices, key=lambda iso: abs(iso - target))
    return str(nearest)


def is_raw_filename(name: str, raw_extensions: tuple[str, ...]) -> bool:
    return name.lower().endswith(raw_extensions)


def pick_raw_choice_from_details(details: dict[str, Any] | None, markers: tuple[str, ...] = ("raw", "nef")) -> str:
    if not details:
        return "RAW"
    choices = details.get("choices") or []
    for choice in choices:
        text = str(choice).strip().lower()
        if any(marker in text for marker in markers):
            return str(choice)
    return "RAW"


def restore_previous_config_value(session, keys: list[str], previous_value: Any | None) -> None:
    if previous_value is None:
        return
    session.write_first_config(keys, previous_value)


def capture_with_route_fallbacks(
    session,
    routes: list[dict[str, str]],
    capture_route_applier,
    raw_filename_checker,
    attempts_per_route: int = 3,
    retry_delay_step_s: float = 0.15,
) -> tuple[bytes, dict[str, Any], dict[str, Any]]:
    """Capture and try fallback routes until a RAW filename is observed."""
    capture_name = ""
    last_error: Exception | None = None

    for route_index, route in enumerate(routes):
        capture_route_applier(session, route)
        for attempt in range(1, attempts_per_route + 1):
            try:
                content, extra = session.capture_image()
            except Exception as exc:
                last_error = exc
                if attempt < attempts_per_route:
                    time.sleep(retry_delay_step_s * attempt)
                    continue
                break

            capture_name = str(extra.get("capture_name", "")).lower()
            if raw_filename_checker(capture_name):
                diagnostics = {
                    "capture_route_index": route_index,
                    "capture_route": route,
                    "capture_attempt": attempt,
                }
                return content, extra, diagnostics

            if attempt < attempts_per_route:
                time.sleep(retry_delay_step_s * attempt)

    if last_error is not None:
        raise RuntimeError(f"All RAW capture routes failed: {last_error}") from last_error
    raise RuntimeError(
        "Camera returned a non-RAW file while RAW was requested "
        f"(last capture_name='{capture_name or 'unknown'}')."
    )
