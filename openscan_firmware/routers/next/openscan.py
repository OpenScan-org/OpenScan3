import asyncio
import os
import zipfile
import glob
from collections import deque
from datetime import datetime
from shutil import disk_usage
from tempfile import NamedTemporaryFile
from typing import AsyncGenerator, Optional, Tuple

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask
from starlette.responses import FileResponse

from openscan_firmware import __version__
from openscan_firmware.config.logger import DEFAULT_LOGS_PATH, flush_memory_handlers
from openscan_firmware.controllers.device import get_scanner_model
from openscan_firmware.utils.dir_paths import resolve_projects_dir, resolve_runtime_dir
from openscan_firmware.utils.firmware_state import get_firmware_state

class DiskUsage(BaseModel):
    """Filesystem usage snapshot for a directory."""

    total: int = Field(..., description="Total bytes available on the filesystem.")
    used: int = Field(..., description="Bytes currently used (total - free).")
    free: int = Field(..., description="Free bytes remaining on the filesystem.")


class SoftwareInfoResponse(BaseModel):
    """Information block served by /next/openscan."""

    model: Optional[str] = Field(None, description="Scanner model identifier, if configured.")
    firmware_version: str = Field(..., description="Currently running firmware version string.")
    last_shutdown_was_unclean: bool = Field(
        ..., description="Indicates whether the previous shutdown finished cleanly."
    )
    runtime_dir: str = Field(..., description="Absolute path used for runtime state files.")
    runtime_disk: Optional[DiskUsage] = Field(
        None, description="Disk usage snapshot for the runtime directory filesystem."
    )
    projects_disk: Optional[DiskUsage] = Field(
        None, description="Disk usage snapshot for the projects directory filesystem."
    )
    uptime_seconds: Optional[float] = Field(
        None, description="Current system uptime in seconds, if available."
    )


router = APIRouter(
    prefix="",
    tags=["openscan"],
    responses={404: {"description": "Not found"}},
)

@router.get("/", response_model=SoftwareInfoResponse)
async def get_software_info() -> SoftwareInfoResponse:
    """Get information about the scanner software"""
    state = get_firmware_state()
    runtime_dir = resolve_runtime_dir()
    projects_dir = resolve_projects_dir()
    return SoftwareInfoResponse(
        model=get_scanner_model(),
        firmware_version=__version__,
        last_shutdown_was_unclean=state.get("last_shutdown_was_unclean", False),
        runtime_dir=str(runtime_dir),
        runtime_disk=_probe_disk_usage(runtime_dir),
        projects_disk=_probe_disk_usage(projects_dir),
        uptime_seconds=_read_uptime_seconds(),
    )


def _probe_disk_usage(path) -> Optional[DiskUsage]:
    try:
        usage = disk_usage(path)
    except (OSError, FileNotFoundError):
        return None
    return DiskUsage(total=usage.total, used=usage.total - usage.free, free=usage.free)


def _read_uptime_seconds() -> Optional[float]:
    try:
        with open("/proc/uptime", "r", encoding="ascii") as handle:
            return float(handle.read().split()[0])
    except (OSError, ValueError, IndexError):
        return None



# -------------------------
# Log utilities and endpoints
# -------------------------

def _read_last_lines(file_path: str, max_lines: int) -> str:
    """Return the last max_lines from file as a single string.

    Args:
        file_path: Path to the file to read.
        max_lines: Maximum number of lines to return.

    Returns:
        The tail content joined by newlines.
    """
    if max_lines <= 0:
        return ""

    lines = deque(maxlen=max_lines)
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                lines.append(line.rstrip("\n"))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Log file not found")
    return "\n".join(lines) + ("\n" if lines else "")


async def _follow_file(file_path: str, poll_interval: float = 1) -> AsyncGenerator[bytes, None]:
    """Async generator that tails a file and yields new lines as bytes.

    Args:
        file_path: Path to the file to follow.
        poll_interval: Sleep interval between checks for new data.

    Yields:
        Bytes chunks representing new lines appended to the file.
    """
    f = None
    last_inode = None
    try:
        while True:
            # Open file if not open yet (or after rotation)
            if f is None:
                try:
                    f = open(file_path, "r", encoding="utf-8", errors="ignore")
                    f.seek(0, os.SEEK_END)
                    last_inode = os.fstat(f.fileno()).st_ino
                except FileNotFoundError:
                    # File might not exist yet or just rotated; retry shortly
                    await asyncio.sleep(poll_interval)
                    continue

            line = f.readline()
            if line:
                yield line.encode("utf-8", errors="ignore")
                continue

            # No new line yet: flush buffered handlers to force write-through
            try:
                flush_memory_handlers()
            except Exception:
                # Non-fatal; keep streaming
                pass

            # Detect rotation by inode change or missing file
            try:
                current_inode = os.stat(file_path).st_ino
                if current_inode != last_inode:
                    try:
                        f.close()
                    finally:
                        f = None
                    continue
            except FileNotFoundError:
                try:
                    f.close()
                finally:
                    f = None
                await asyncio.sleep(poll_interval)
                continue

            await asyncio.sleep(poll_interval)
    except asyncio.CancelledError:
        if f is not None:
            try:
                f.close()
            except Exception:
                pass
        return
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Log file not found")


@router.get("/logs/tail")
async def tail_logs(format: str = "text", lines: int = 200, follow: bool = False, poll_interval: float = 1):
    """Show or follow current logs.

    When follow=false (default), returns the last N lines of the selected log.
    When follow=true (text mode only!), streams new lines as they are written (like `tail -f`).

    Args:
        format: "text" for openscan_firmware.log, "json" for openscan_detailed_log.json.
        lines: Number of last lines to return initially.
        follow: If true, stream appended log lines in text mode.
        poll_interval: Poll interval (seconds) when following in text mode.

    Returns:
        A response with the requested log content.
    """
    flush_memory_handlers()  # Ensure buffered records are flushed to disk

    if format.lower() == "json":
        log_file = os.path.join(DEFAULT_LOGS_PATH, "openscan_detailed_log.json")
        media_type = "application/json"
    else:
        log_file = os.path.join(DEFAULT_LOGS_PATH, "openscan_firmware.log")
        media_type = "text/plain"

    if not os.path.exists(log_file):
        raise HTTPException(status_code=404, detail="Log file not found")

    if follow and format.lower() == "text":
        # Send last N lines first, then follow new lines
        async def stream() -> AsyncGenerator[bytes, None]:
            head = _read_last_lines(log_file, lines).encode("utf-8")
            if head:
                yield head
            async for chunk in _follow_file(log_file, poll_interval=poll_interval):
                yield chunk
        headers = {
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx buffering if present
            "Connection": "keep-alive",
        }
        return StreamingResponse(stream(), media_type=media_type, headers=headers)

    # One-shot tail of last N lines
    content = _read_last_lines(log_file, lines)
    return StreamingResponse(iter([content.encode("utf-8")]), media_type=media_type)


@router.get("/logs/archive")
async def download_logs_archive():
    """Create and download a ZIP archive containing all log files.

    The archive includes rotated files for both text and JSON logs, using
    deflate compression for reasonable size to share e.g. via email.

    Returns:
        FileResponse serving the generated ZIP. The temp file is deleted after send.
    """
    flush_memory_handlers()  # Flush buffered logs before archiving

    patterns = [
        os.path.join(DEFAULT_LOGS_PATH, "openscan_firmware.log*"),
        os.path.join(DEFAULT_LOGS_PATH, "openscan_detailed_log.json*"),
    ]
    files = []
    for pat in patterns:
        files.extend(glob.glob(pat))
    files = [f for f in files if os.path.isfile(f)]

    if not files:
        raise HTTPException(status_code=404, detail="No log files found to archive")

    # Create a temporary zip file and return it; delete after response is sent
    tmp = NamedTemporaryFile(delete=False, suffix=".zip")
    tmp_path = tmp.name
    tmp.close()

    # Use maximum compression level for smaller email-friendly files
    compression = zipfile.ZIP_DEFLATED
    compresslevel = 9  # Python 3.7+ supports compresslevel for ZipFile
    with zipfile.ZipFile(tmp_path, mode="w", compression=compression, compresslevel=compresslevel) as zf:
        for fpath in files:
            arcname = os.path.basename(fpath)
            zf.write(fpath, arcname=arcname)

    filename = f"openscan_logs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"

    def _cleanup(path: str) -> None:
        try:
            os.remove(path)
        except OSError:
            pass

    return FileResponse(
        tmp_path,
        media_type="application/zip",
        filename=filename,
        background=BackgroundTask(_cleanup, tmp_path),
    )
