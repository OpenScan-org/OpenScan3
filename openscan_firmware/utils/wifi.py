"""WiFi QR code parsing and network configuration utilities.

Parses the standard WiFi QR code format used by Android and iOS share features
and applies the credentials via NetworkManager (nmcli).
"""

from __future__ import annotations

import logging
import re
import subprocess
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class WifiCredentials:
    """Parsed WiFi credentials from a QR code string.

    Attributes:
        ssid: The network name.
        password: The network password (empty string for open networks).
        security: Security type, e.g. "WPA", "WEP", or "nopass".
        hidden: Whether the network is hidden.
    """
    ssid: str
    password: str = ""
    security: str = "WPA"
    hidden: bool = False


def parse_wifi_qr(raw: str) -> WifiCredentials:
    """Parse an Android/iOS WiFi share QR code string.

    The expected format is::

        WIFI:T:<security>;S:<ssid>;P:<password>;H:<hidden>;;

    Fields may appear in any order. The ``T``, ``H``, and ``P`` fields are
    optional.  Semicolons inside values can be escaped with a backslash.

    Args:
        raw: The raw string decoded from a QR code.

    Returns:
        WifiCredentials with the extracted values.

    Raises:
        ValueError: If the string is not a valid WiFi QR code or the SSID is
            missing.
    """
    if not raw.startswith("WIFI:"):
        raise ValueError(f"Not a WiFi QR code string: {raw!r}")

    # Strip the "WIFI:" prefix and trailing ";;"
    body = raw[5:]
    if body.endswith(";;"):
        body = body[:-2]

    fields: dict[str, str] = {}
    # Match key:value pairs, allowing escaped semicolons inside values
    for match in re.finditer(r"([TSPH]):((\\.|[^;])*)(?:;|$)", body):
        key = match.group(1)
        # Unescape backslash-escaped characters
        value = re.sub(r"\\(.)", r"\1", match.group(2))
        fields[key] = value

    ssid = fields.get("S", "").strip()
    if not ssid:
        raise ValueError("WiFi QR code is missing the SSID (S field)")

    return WifiCredentials(
        ssid=ssid,
        password=fields.get("P", ""),
        security=fields.get("T", "WPA"),
        hidden=fields.get("H", "").lower() == "true",
    )


def connect_wifi(
    credentials: WifiCredentials,
    *,
    max_attempts: int = 2,
    rescan_delay: float = 1.0,
) -> str:
    """Connect to a WiFi network using NetworkManager (nmcli).

    This requires the process to have sufficient privileges (typically root)
    to modify network connections.

    Args:
        credentials: The WiFi credentials to use.
        max_attempts: How many times to attempt the connection before giving up.
            On retries the helper performs an ``nmcli device wifi rescan`` first.
        rescan_delay: Seconds to wait after triggering the rescan to give the
            kernel time to refresh the scan list. Ignored if non-positive.

    Returns:
        The stdout output from nmcli on success.

    Raises:
        RuntimeError: If nmcli is not available or the connection attempt fails.
    """
    ensure_wifi_radio_enabled()

    attempts = max(1, max_attempts)
    cmd = [
        "nmcli", "device", "wifi", "connect", credentials.ssid,
        "password", credentials.password,
    ]

    if credentials.hidden:
        cmd.extend(["hidden", "yes"])

    logger.info("Attempting to connect to WiFi network '%s'", credentials.ssid)

    for attempt in range(1, attempts + 1):
        logger.debug(
            "Running (attempt %d/%d): %s",
            attempt,
            attempts,
            " ".join(cmd[:5]) + " ****",
        )

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode == 0:
            logger.info("Successfully connected to WiFi network '%s'", credentials.ssid)
            return result.stdout.strip()

        error_msg = result.stderr.strip() or result.stdout.strip()
        logger.error("nmcli failed (rc=%d): %s", result.returncode, error_msg)

        should_retry = (
            attempt < attempts and
            "not in the scan list" in error_msg.lower()
        )

        if should_retry:
            logger.warning(
                "Access point not in scan list; triggering nmcli rescan before retry %d/%d.",
                attempt + 1,
                attempts,
            )
            _rescan_wifi_devices()
            if rescan_delay > 0:
                time.sleep(rescan_delay)
            continue

        raise RuntimeError(f"Failed to connect to '{credentials.ssid}': {error_msg}")

    raise RuntimeError(
        f"Failed to connect to '{credentials.ssid}' after {attempts} attempts: {error_msg}"
    )


def is_wifi_connected() -> bool:
    """Check whether any WiFi device is currently connected.

    Returns:
        True if at least one WiFi device reports a connected state.
    """
    return "wifi" in _get_connected_device_types()


def is_ethernet_connected() -> bool:
    """Check whether any Ethernet device is currently connected.

    Returns:
        True if at least one Ethernet device reports a connected state.
    """
    return "ethernet" in _get_connected_device_types()


def is_network_ready_for_qr_scan() -> bool:
    """Return True when WiFi or Ethernet is already connected.

    Returns:
        True when a network connection already exists and QR setup is unnecessary.
    """
    connected_types = _get_connected_device_types()
    return "wifi" in connected_types or "ethernet" in connected_types


def _get_connected_device_types() -> set[str]:
    """Return connected NetworkManager device types from nmcli."""
    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "TYPE,STATE", "device"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        logger.warning("Could not check network status: %s", exc)
        return set()

    connected_types: set[str] = set()
    for line in result.stdout.splitlines():
        parts = line.split(":")
        if len(parts) >= 2 and parts[1] == "connected":
            connected_types.add(parts[0])

    return connected_types


def _rescan_wifi_devices() -> None:
    """Trigger an nmcli rescan, ignoring errors but logging them."""
    try:
        subprocess.run(
            ["nmcli", "device", "wifi", "rescan"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        logger.warning("nmcli rescan failed: %s", exc)


def _run_nmcli_command(command: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    """Run an nmcli command and normalize common subprocess errors."""
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("nmcli is not available on this system") from exc
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip() if exc.stderr else str(exc)
        raise RuntimeError(f"Failed to run '{' '.join(command)}': {stderr}") from exc


def is_wifi_radio_enabled(timeout: float = 5.0) -> bool:
    """Return True if NetworkManager reports the WiFi radio as enabled."""
    result = _run_nmcli_command(["nmcli", "radio", "wifi"], timeout=timeout)
    state = result.stdout.strip().lower()
    return state == "enabled"


def ensure_wifi_radio_enabled(timeout: float = 5.0) -> None:
    """Enable the WiFi radio if it is currently disabled."""
    if is_wifi_radio_enabled(timeout=timeout):
        logger.debug("WiFi radio already enabled – skipping toggle.")
        return

    logger.info("WiFi radio disabled – enabling via nmcli.")
    _run_nmcli_command(["nmcli", "radio", "wifi", "on"], timeout=timeout)
