"""GPhoto2 camera controller package."""

from __future__ import annotations

from typing import Any

__all__ = ["GPhoto2Controller", "Gphoto2Camera"]


def __getattr__(name: str) -> Any:
    if name in {"GPhoto2Controller", "Gphoto2Camera"}:
        from .controller import GPhoto2Controller, Gphoto2Camera

        return {"GPhoto2Controller": GPhoto2Controller, "Gphoto2Camera": Gphoto2Camera}[name]
    raise AttributeError(name)
