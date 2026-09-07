"""DerivedPath — a Nix derived path expression, plain string on the wire."""

from __future__ import annotations

from .wire_scalar import WireScalar


class DerivedPath(WireScalar):
    """A Nix derived path — ``drv!out`` format, plain string on wire."""

    def __new__(cls, value: str = "", *, derived_path: str | None = None) -> DerivedPath:
        if derived_path is not None:
            if value and value != derived_path:
                raise ValueError("DerivedPath value and derived_path disagree")
            value = derived_path
        return super().__new__(cls, value)

    @property
    def value(self) -> str:
        """Compatibility spelling for the canonical string value."""
        return self
