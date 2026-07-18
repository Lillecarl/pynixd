"""DerivedPath — a Nix derived path expression, plain string on the wire."""

from __future__ import annotations

from .wire_string import WireString


class DerivedPath(WireString):
    """A Nix derived path — ``drv!out`` format, plain string on wire."""

    value: str
