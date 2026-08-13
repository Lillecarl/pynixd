"""NARHash — base16-encoded NAR SHA256 hash as a WireString."""

from __future__ import annotations

from .wire_scalar import WireScalar


class NARHash(WireScalar):
    """Base16-encoded NAR SHA256 hash — no algorithm prefix on wire."""

    def __new__(cls, value: str = "", *, hash: str | None = None) -> NARHash:  # noqa: A002
        if hash is not None:
            if value and value != hash:
                raise ValueError("NARHash value and hash disagree")
            value = hash
        return super().__new__(cls, value)

    @property
    def hash(self) -> str:
        """Compatibility spelling for the canonical string value."""
        return self
