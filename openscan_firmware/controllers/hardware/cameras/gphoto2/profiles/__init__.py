"""Built-in GPhoto2 camera profiles."""

from .canon_eos_700d import CanonEOS700DProfile
from .generic import GenericGPhoto2Profile
from .nikon_d7100 import NikonD7100Profile

__all__ = ["CanonEOS700DProfile", "NikonD7100Profile", "GenericGPhoto2Profile"]
