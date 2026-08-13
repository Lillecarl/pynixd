"""Reusable type aliases for common complex types."""

from __future__ import annotations

from .store_path import StorePath

type OutputName = str
"""Name of a derivation output (e.g. "out", "bin", "lib")."""

type OutputMap = dict[StorePath, dict[OutputName, StorePath | None]]
"""Map of derivation paths to their output maps."""

type StorePathSet = set[StorePath]
"""Set of store paths — the protocol ``Set<StorePath>``."""

type NARHash = str
"""Base16-encoded SHA256 NAR hash without algorithm prefix."""

type ContentAddress = str
"""Content-addressed store path identifier in ``method:hash`` format."""
