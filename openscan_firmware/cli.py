"""Command-line interface for OpenScan3.

Provides an argparse-based CLI with a `serve` subcommand to start the
FastAPI application using uvicorn.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

import uvicorn


DEFAULT_RELOAD_TRIGGER = Path(__file__).resolve().parents[1] / ".reload-trigger"


def _build_parser() -> argparse.ArgumentParser:
    """Create the top-level argument parser (hybrid mode).

    In hybrid mode, running `openscan` without a subcommand defaults to
    starting the API server (equivalent to `openscan serve`).

    Returns:
        argparse.ArgumentParser: Configured parser with `serve` (alias: `start`).
    """
    # Common options shared by top-level and subcommands
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0)")
    common.add_argument("--port", type=int, default=8000, help="Bind port (default: 8000)")
    common.add_argument(
        "--reload-trigger",
        action="store_true",
        help="Enable reloads driven by the project-level .reload-trigger sentinel file.",
    )
    common.add_argument(
        "--root-path",
        default="",
        help="Root path for reverse proxy (e.g., '/api' when served under /api/).",
    )

    parser = argparse.ArgumentParser(
        prog="openscan",
        parents=[common],
        description=(
            "OpenScan3 - Raspberry Pi based photogrammetry scanner (FastAPI app).\n"
            "Run without a subcommand to start the API server, or use `serve`/`start`."
        ),
    )

    # Default action when no subcommand is provided
    parser.set_defaults(func=_cmd_serve)

    subparsers = parser.add_subparsers(dest="command")

    # serve subcommand (alias: start)
    serve = subparsers.add_parser(
        "serve",
        parents=[common],
        help="Start the FastAPI application via uvicorn",
        description="Start the API service (uvicorn openscan_firmware.main:app)",
        aliases=["start"],
    )
    serve.set_defaults(func=_cmd_serve)

    return parser


def _cmd_serve(
    host: str,
    port: int,
    reload_trigger: bool,
    root_path: str,
) -> int:
    """Start the FastAPI app using uvicorn.

    Args:
        host: Host interface to bind to.
        port: TCP port to bind to.
        reload_trigger: Whether to enable reloads via the .reload-trigger sentinel file.
        root_path: Root path prefix for reverse proxy setups.

    Returns:
        Exit status code (0 on success).
    """
    # Import by string to avoid importing the app at CLI parse time.
    reload_enabled = reload_trigger
    reload_dirs = [str(DEFAULT_RELOAD_TRIGGER.parent)] if reload_trigger else None
    reload_includes = [DEFAULT_RELOAD_TRIGGER.name] if reload_trigger else None
    reload_excludes = ["*.py", "*.pyc", "*.pyi", "*.pyd", "*.pyo"] if reload_trigger else None

    uvicorn.run(
        "openscan_firmware.main:app",
        host=host,
        port=port,
        root_path=root_path,
        reload=reload_enabled,
        reload_dirs=reload_dirs,
        reload_includes=reload_includes,
        reload_excludes=reload_excludes,
    )
    return 0


def main(argv: Optional[list[str]] = None) -> None:
    """CLI entry point.

    Args:
        argv: Optional list of arguments. Defaults to sys.argv[1:].

    Notes:
        This function is used by both the console_script `openscan` and
        by module execution via `python -m openscan`.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    # Execute resolved command (defaults to serving the API)
    code = args.func(
        host=args.host,
        port=args.port,
        reload_trigger=args.reload_trigger,
        root_path=args.root_path,
    )

    if code != 0:
        sys.exit(code)
