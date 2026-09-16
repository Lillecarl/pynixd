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
"""Base16-encoded SHA256 NAR hash without algorithm prefix.

**This shadows `nar_hash.NARHash`, which is the real class, and it is not
a simple mistake to correct.** `NARHash` strips a known algorithm prefix
when it is built, so no value of that class can carry `sha256:`. Its one
consumer, `pynixd.signing.fingerprint`, has a branch for a prefixed
string that six tests exercise and no caller can reach once the parameter
names the class. Removing that branch is a change to the signing path,
and the comment above it records the bug it was written for. pynixd#3.
"""
