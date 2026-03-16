"""WiFi QR code parsing and network configuration utilities.

Parses the standard WiFi QR code format used by Android and iOS share features
and applies the credentials via NetworkManager (nmcli).
"""

from __future__ import annotations

import logging
import re
import subprocess
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


def connect_wifi(credentials: WifiCredentials) -> str:
    """Connect to a WiFi network using NetworkManager (nmcli).

    This requires the process to have sufficient privileges (typically root)
    to modify network connections.

    Args:
        credentials: The WiFi credentials to use.

    Returns:
        The stdout output from nmcli on success.

    Raises:
        RuntimeError: If nmcli is not available or the connection attempt fails.
    """
    cmd = [
        "nmcli", "device", "wifi", "connect", credentials.ssid,
        "password", credentials.password,
    ]

    if credentials.hidden:
        cmd.extend(["hidden", "yes"])

    logger.info("Attempting to connect to WiFi network '%s'", credentials.ssid)
    logger.debug("Running: %s", " ".join(cmd[:5]) + " ****")  # mask password

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=30,
    )

    if result.returncode != 0:
        error_msg = result.stderr.strip() or result.stdout.strip()
        logger.error("nmcli failed (rc=%d): %s", result.returncode, error_msg)
        raise RuntimeError(f"Failed to connect to '{credentials.ssid}': {error_msg}")

    logger.info("Successfully connected to WiFi network '%s'", credentials.ssid)
    return result.stdout.strip()


def is_wifi_connected() -> bool:
    """Check whether any WiFi device is currently connected.

    Returns:
        True if at least one WiFi device reports a connected state.
    """
    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "TYPE,STATE", "device"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        for line in result.stdout.splitlines():
            parts = line.split(":")
            if len(parts) >= 2 and parts[0] == "wifi" and parts[1] == "connected":
                return True
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        logger.warning("Could not check WiFi status: %s", exc)
    return False
