"""Signature — a Nix cryptographic signature as a WireString."""

from .wire_string import WireString


class Signature(WireString):
    """A Nix signature — "name:signature" on the wire."""

    name: str = ""
    signature: str = ""

    @classmethod
    def from_str(cls, data: str) -> dict[str, str]:
        parts = data.split(":", 1)
        return {"name": parts[0], "signature": parts[1] if len(parts) > 1 else ""}

    def to_str(self) -> str:
        return f"{self.name}:{self.signature}"
