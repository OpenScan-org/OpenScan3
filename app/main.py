"""Compatibility shim for legacy `app.main` imports.

This module re-exports the FastAPI application instance from
`openscan.main` to keep `from app.main import app` working during the
migration.

Temporary; will be removed in Phase 4.
"""

from openscan.main import app  # noqa: F401
