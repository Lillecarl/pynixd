"""DrvOutput — a derivation output identifier on the wire.

Wire format: plain string like ``sha256:abc123!out``.
"""

from __future__ import annotations

from .wire_string import WireString


class DrvOutput(WireString):
    """DrvOutput on the wire — ``"drvHash!outputName"`` format.

    Uses the same ``from_str``/``to_str`` pattern as ``Signature``
    but with ``!`` as the delimiter instead of ``:``.
    """

    drv_hash: str = ""
    output_name: str = ""

    @classmethod
    def from_str(cls, data: str) -> object:
        parts = data.split("!", 1)
        return {"drv_hash": parts[0], "output_name": parts[1] if len(parts) > 1 else ""}

    def to_str(self) -> str:
        return f"{self.drv_hash}!{self.output_name}"
