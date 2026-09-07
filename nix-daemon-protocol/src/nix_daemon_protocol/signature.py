"""Signature — a Nix cryptographic signature as a canonical wire scalar."""

from __future__ import annotations

from .wire_scalar import WireScalar


class Signature(WireScalar):
    """A Nix signature — "name:signature" on the wire."""

    def __new__(
        cls,
        value: str = "",
        *,
        name: str | None = None,
        signature: str | None = None,
    ) -> Signature:
        if name is not None or signature is not None:
            composed = f"{name or ''}:{signature or ''}"
            if value and value != composed:
                raise ValueError("Signature value and name/signature disagree")
            value = composed
        return super().__new__(cls, value)

    @classmethod
    def from_str(cls, data: str) -> dict[str, str]:
        parts = data.split(":", 1)
        return {"name": parts[0], "signature": parts[1] if len(parts) > 1 else ""}

    @property
    def name(self) -> str:
        """The signing key name before the first colon."""
        return self.partition(":")[0]

    @property
    def signature(self) -> str:
        """The encoded signature after the first colon."""
        return self.partition(":")[2]

    def to_str(self) -> str:
        """Compatibility spelling for the canonical string value."""
        return self
