"""Registry for selecting a GPhoto2 profile based on camera identity."""

from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil

from .profile import CameraIdentity, GPhoto2Profile
from .profiles.generic import GenericGPhoto2Profile

logger = logging.getLogger(__name__)


def _iter_profile_classes() -> list[type[GPhoto2Profile]]:
    profile_classes: list[type[GPhoto2Profile]] = []

    # Import every module in the profiles package so new profile files are
    # discovered automatically without manual registry edits.
    import openscan_firmware.controllers.hardware.cameras.gphoto2.profiles as profiles_package

    for module_info in pkgutil.iter_modules(profiles_package.__path__, profiles_package.__name__ + "."):
        try:
            module = importlib.import_module(module_info.name)
        except Exception:
            logger.exception("Failed to import gphoto2 profile module '%s'.", module_info.name)
            continue

        for _, class_obj in inspect.getmembers(module, inspect.isclass):
            if not issubclass(class_obj, GPhoto2Profile):
                continue
            if class_obj is GPhoto2Profile:
                continue
            if not getattr(class_obj, "register_in_registry", True):
                continue
            if class_obj in profile_classes:
                continue
            profile_classes.append(class_obj)

    return _sorted_profile_classes(profile_classes)


def _sorted_profile_classes(profile_classes: list[type[GPhoto2Profile]]) -> list[type[GPhoto2Profile]]:
    # Keep generic as final fallback regardless of module filename ordering.
    generic_classes: list[type[GPhoto2Profile]] = []
    specific_classes: list[type[GPhoto2Profile]] = []

    for profile_class in profile_classes:
        if profile_class is GenericGPhoto2Profile or getattr(profile_class, "profile_id", "") == "generic":
            generic_classes.append(profile_class)
        else:
            specific_classes.append(profile_class)

    specific_classes.sort(key=lambda cls: f"{cls.__module__}.{cls.__name__}")
    if not generic_classes:
        generic_classes = [GenericGPhoto2Profile]
    return specific_classes + generic_classes


_PROFILE_CLASSES: list[type[GPhoto2Profile]] = _iter_profile_classes()


def get_profile_for_identity(identity: CameraIdentity) -> GPhoto2Profile:
    for profile_cls in _PROFILE_CLASSES:
        profile = profile_cls()
        if profile.matches(identity):
            return profile
    return GenericGPhoto2Profile()
