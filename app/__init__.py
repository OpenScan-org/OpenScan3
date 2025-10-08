"""Compatibility shim package for legacy `app.*` imports.

This package aliases legacy import paths like `app.config.logger` to the
new package locations under `openscan.*`. It allows progressive refactors
from `app.*` to `openscan.*` without breaking external or internal
consumers during the migration.

This shim is temporary and will be removed in Phase 4.
"""

from __future__ import annotations

import importlib
import sys
from types import ModuleType


def _alias_module(legacy: str, new: str) -> ModuleType:
    """Import `new` and alias it as `legacy` in sys.modules.

    Args:
        legacy: Legacy module name to expose (e.g., "app.config.logger").
        new: New module name to import (e.g., "openscan.config.logger").

    Returns:
        The imported module object.
    """
    mod = importlib.import_module(new)
    sys.modules[legacy] = mod
    return mod


# Provide a direct alias to the top-level new package for convenience.
sys.modules.setdefault("app", sys.modules[__name__])

# Pre-alias common top-level packages so nested imports resolve reliably.
_alias_module("app.main", "openscan.main")
_alias_module("app.config", "openscan.config")
_alias_module("app.controllers", "openscan.controllers")
_alias_module("app.models", "openscan.models")
_alias_module("app.routers", "openscan.routers")
_alias_module("app.tasks", "openscan.tasks")
_alias_module("app.utils", "openscan.utils")
