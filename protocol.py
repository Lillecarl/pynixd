"""
Nix daemon protocol types and operation codes.
Protocol versions 1.32+ are supported (negotiated per connection).
"""

from . import serde as serde
from .serde.wire_ops import WIRE_REGISTRY


def get_extension_features() -> set[str]:
    """Return all pynixd extension operation names."""
    return {cls.name for cls in WIRE_REGISTRY.values() if cls.is_extension}
