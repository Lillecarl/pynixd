"""DrvOutput — a derivation output identifier as a canonical wire scalar.

Wire format: plain string like ``sha256:abc123!out``.
"""

from __future__ import annotations

from .wire_scalar import WireScalar


class DrvOutput(WireScalar):
    """DrvOutput on the wire — ``"drvHash!outputName"`` format.

    Uses the same ``from_str``/``to_str`` pattern as ``Signature``
    but with ``!`` as the delimiter instead of ``:``.
    """

    def __new__(
        cls,
        value: str = "",
        *,
        drv_hash: str | None = None,
        output_name: str | None = None,
    ) -> DrvOutput:
        if drv_hash is not None or output_name is not None:
            composed = f"{drv_hash or ''}!{output_name or ''}"
            if value and value != composed:
                raise ValueError("DrvOutput value and drv_hash/output_name disagree")
            value = composed
        return super().__new__(cls, value)

    @classmethod
    def from_str(cls, data: str) -> object:
        parts = data.split("!", 1)
        return {"drv_hash": parts[0], "output_name": parts[1] if len(parts) > 1 else ""}

    @property
    def drv_hash(self) -> str:
        """The derivation hash before the first exclamation mark."""
        return self.partition("!")[0]

    @property
    def output_name(self) -> str:
        """The output name after the first exclamation mark."""
        return self.partition("!")[2]

    def to_str(self) -> str:
        """Compatibility spelling for the canonical string value."""
        return self
