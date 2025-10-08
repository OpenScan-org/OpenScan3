"""Compatibility shim: `app.models` -> `openscan.models`.
Temporary; removed in Phase 4.
"""
from importlib import import_module as _im
import sys as _sys
_sys.modules[__name__] = _im("openscan.models")
