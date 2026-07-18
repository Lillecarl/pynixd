"""StorePath — a Nix store path as a WireString."""

from __future__ import annotations

from pathlib import Path

from pydantic import model_validator

from .wire_string import WireString


class StorePath(WireString):
    """A store path — single string field."""

    path: str

    @model_validator(mode="before")
    @classmethod
    def transform(cls, data: object) -> object:
        if isinstance(data, dict):
            return data
        if isinstance(data, str):
            return cls.from_str(data)
        if hasattr(data, "__str__"):
            return cls.from_str(str(data))
        return data

    def endswith(self, suffix: str) -> bool:
        return self.path.endswith(suffix)

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
        return StorePath(path=f"/nix/store/{self.hash_part()}")
