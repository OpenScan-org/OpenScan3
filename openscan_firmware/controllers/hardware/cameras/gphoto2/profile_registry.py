"""Registry for selecting a GPhoto2 profile based on camera identity."""

from __future__ import annotations

from .profile import CameraIdentity, GPhoto2Profile
from .profiles import CanonEOS700DProfile, NikonD7100Profile, GenericGPhoto2Profile

_PROFILE_CLASSES: list[type[GPhoto2Profile]] = [
    CanonEOS700DProfile,
    NikonD7100Profile,
    GenericGPhoto2Profile,
]


def get_profile_for_identity(identity: CameraIdentity) -> GPhoto2Profile:
    for profile_cls in _PROFILE_CLASSES:
        profile = profile_cls()
        if profile.matches(identity):
            return profile
    return GenericGPhoto2Profile()
