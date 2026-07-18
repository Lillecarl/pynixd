"""
StorePath: a self-owned class for Nix store paths (no longer a str subclass).
DrvOutput: a class for Nix derivation output identifiers.
"""

from __future__ import annotations

import json as _json
from pathlib import Path
from typing import Any

_original_default = _json.JSONEncoder.default

_STORE_PREFIX = "/nix/store/"


class StorePath:
    """A Nix store path.

    Stores the bare path (hash-name) internally — the ``/nix/store/`` prefix
    is stripped on construction and re-added by ``__str__``.

    Provides helpers for basename, derivation checking, etc.
    Can store optional 'extrainfo' for debugging (e.g. why this path is required).
    """

    __slots__ = ("_path", "extrainfo")

    def __init__(self, path: str | StorePath, extrainfo: Any = None) -> None:
        if isinstance(path, StorePath):
            self._path = path._path
            self.extrainfo = extrainfo or path.extrainfo
        else:
            self._path = self._strip_prefix(path)
            self.extrainfo = extrainfo

    @staticmethod
    def _strip_prefix(path: str) -> str:
        if path.startswith(_STORE_PREFIX):
            return path[len(_STORE_PREFIX) :]
        return path

    # ── Core accessors ─────────────────────────────────────────────

    def base(self) -> str:
        """The bare path (hash-name) without the ``/nix/store/`` prefix."""
        return self._path

    @property
    def name(self) -> str:
        """The full basename (hash-name) of the store path."""
        return Path(self._path).name

    def hash_part(self) -> str:
        """The 32-character hash part of the store path."""
        return self.name.split("-", 1)[0]

    def base_name(self) -> str:
        """The human-readable name part (after the hash)."""
        parts = self.name.split("-", 1)
        return parts[1] if len(parts) > 1 else ""

    def is_derivation(self) -> bool:
        """Return True if this is a .drv path."""
        return self._path.endswith(".drv")

    def to_path(self) -> Path:
        """Convert to a pathlib.Path (full ``/nix/store/...`` path)."""
        return Path(str(self))

    def with_store_prefix(self) -> StorePath:
        """Return self — ``__str__`` already guarantees the ``/nix/store/`` prefix."""
        return self

    # ── str-adjacent helpers ───────────────────────────────────────

    def endswith(self, suffix: str) -> bool:
        """Check whether the bare path ends with *suffix*."""
        return self._path.endswith(suffix)

    def startswith(self, prefix: str) -> bool:
        """Check whether the bare path starts with *prefix*."""
        return self._path.startswith(prefix)

    # ── Dunder methods ─────────────────────────────────────────────

    def __str__(self) -> str:
        """Full store path: ``/nix/store/{bare}`` (or ``""`` when empty)."""
        if not self._path:
            return ""
        return _STORE_PREFIX + self._path

    def __repr__(self) -> str:
        inner = repr(self._path)
        if self.extrainfo:
            return f"StorePath({inner}, info={self.extrainfo!r})"
        return f"StorePath({inner})"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, StorePath):
            return self._path == other._path
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self._path)

    def __bool__(self) -> bool:
        return bool(self._path)

    def __len__(self) -> int:
        return len(self._path)

    def __lt__(self, other: object) -> bool:
        if isinstance(other, StorePath):
            return self._path < other._path
        return NotImplemented

    def __le__(self, other: object) -> bool:
        if isinstance(other, StorePath):
            return self._path <= other._path
        return NotImplemented

    def __json__(self) -> str:
        """JSON serialization — returns the full store path."""
        return str(self)


class DrvOutput:
    """A Nix derivation output identifier.

    Format: ``<hash-algo>:<base16-hash>!<outputName>``
    Example: ``sha256:ba0770319c4c4c5f849e8e0e4a8b9c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8!out``

    The hash is the derivation's ``hashDerivationModulo`` — NOT the .drv store path.

    Can be constructed from a string or from keyword components::

        DrvOutput("sha256:abc!out")
        DrvOutput(hash_algo="sha256", hash_value="abc", output_name="out")
    """

    def __init__(
        self,
        value: str = "",
        *,
        hash_algo: str | None = None,
        hash_value: str | None = None,
        output_name: str | None = None,
        path: str = "",
    ) -> None:
        if isinstance(value, DrvOutput):
            self._hash_algo = value._hash_algo
            self._hash_value = value._hash_value
            self._output_name = value._output_name
            self._path = value._path
            return
        if hash_algo is not None:
            self._hash_algo = hash_algo
            self._hash_value = hash_value or ""
            self._output_name = output_name or ""
            self._path = path
        else:
            if value and "!" not in value:
                raise ValueError(
                    f"Invalid DrvOutput: {value!r} — expected format '<algo>:<hash>!<outputName>'",
                )
            if value:
                id_hash, self._output_name = value.split("!", 1)
                # algo can have colons (e.g. "r:sha256"), so split from right
                *algo_parts, self._hash_value = id_hash.split(":")
                self._hash_algo = ":".join(algo_parts)
            else:
                self._hash_algo = ""
                self._hash_value = ""
                self._output_name = ""
            self._path = ""

    @property
    def hash_algo(self) -> str:
        return self._hash_algo

    @property
    def hash_value(self) -> str:
        return self._hash_value

    @property
    def output_name(self) -> str:
        return self._output_name

    @property
    def name(self) -> str:
        """Alias for ``output_name`` — matches the ``OutputInfo`` field."""
        return self._output_name

    @property
    def path(self) -> str:
        """The output store path, or empty for CA derivations."""
        return self._path

    @property
    def id_hash(self) -> str:
        """The hash-algorithm-prefixed hash part (before '!')."""
        return f"{self._hash_algo}:{self._hash_value}"

    def __str__(self) -> str:
        if not self._hash_algo and not self._hash_value and not self._output_name:
            return ""
        return f"{self._hash_algo}:{self._hash_value}!{self._output_name}"

    def __repr__(self) -> str:
        return f"DrvOutput({str(self)!r})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, DrvOutput):
            return NotImplemented
        return (
            self._hash_algo == other._hash_algo
            and self._hash_value == other._hash_value
            and self._output_name == other._output_name
        )

    def __hash__(self) -> int:
        return hash((self._hash_algo, self._hash_value, self._output_name))
