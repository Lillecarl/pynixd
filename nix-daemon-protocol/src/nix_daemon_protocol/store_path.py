"""StorePath — a Nix store path as a WireString."""

from __future__ import annotations

from pathlib import Path

from .wire_scalar import WireScalar


class StorePath(WireScalar):
    """A store path — single string field."""

    def __new__(cls, value: str = "", *, path: str | None = None) -> StorePath:
        if path is not None:
            if value and value != path:
                raise ValueError("StorePath value and path disagree")
            value = path
        return super().__new__(cls, value)

    @property
    def path(self) -> str:
        """Compatibility spelling for the canonical string value.

        `str(self)`, and not `self`. A `WireScalar` is a `str`, so `self`
        satisfies the annotation and reads as correct. It is not: it makes
        `self.path.<method>()` dispatch back to this class. There was an
        `endswith` here that did exactly that, and every call recursed until
        the interpreter raised RecursionError. `str(self)` is a plain `str`,
        so a method of this class can delegate to `self.path` and reach `str`.
        """
        return str(self)

    @property
    def name(self) -> str:
        """The full basename (hash-name) of the store path."""
        return Path(self.path).name

    def hash_part(self) -> str:
        return self.name.split("-", 1)[0]

    def is_derivation(self) -> bool:
        """Return True if this is a .drv path."""
        return self.path.endswith(".drv")

    def with_store_prefix(self) -> StorePath:
        """Return a StorePath guaranteed to have /nix/store/ prefix."""
        if self.path.startswith("/nix/store/"):
            return self
        return StorePath(f"/nix/store/{self.hash_part()}")
