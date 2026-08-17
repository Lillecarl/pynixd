"""The directory that holds the store paths of the store this process serves.

Nix keeps the same value in `settings.nixStore`, and it is one value for the
whole process. A store path on the wire is a text such as
`/nix/store/<hash>-<name>`, and the client and the daemon must agree on the
part before the hash. Nix does not negotiate that part in the handshake, so a
client of a store at another directory gets an error and not a translation.

The value comes from three places, in this order:

1. `set_store_dir()`, which the daemon calls with the directory of the store
   it serves.
2. `NIX_STORE_DIR` in the environment, which Nix itself reads.
3. `/nix/store`.

This module holds the value, and not `pynixd`, because
`nix_daemon_protocol` decodes a store path and must not import `pynixd`.
"""

from __future__ import annotations

import os

DEFAULT_STORE_DIR = "/nix/store"

_store_dir: str | None = None


def store_dir() -> str:
    """The store directory, with no separator at the end."""
    global _store_dir
    if _store_dir is None:
        _store_dir = os.environ.get("NIX_STORE_DIR", "").rstrip("/") or DEFAULT_STORE_DIR
    return _store_dir


def store_prefix() -> str:
    """The store directory with a separator at the end."""
    return store_dir() + "/"


def set_store_dir(path: str | os.PathLike[str]) -> None:
    """Give the process the directory of the store that it serves.

    The daemon calls this once, before it accepts a connection.
    """
    global _store_dir
    value = str(path).rstrip("/")
    if not value.startswith("/"):
        raise ValueError(f"the store directory must be an absolute path: {path!r}")
    _store_dir = value


def reset_store_dir() -> None:
    """Forget the value, so that the next reader finds it again.

    A test uses this. Production code calls `set_store_dir` instead.
    """
    global _store_dir
    _store_dir = None


def in_store_dir(path: str) -> bool:
    """True when *path* is a path inside the store directory."""
    return path.startswith(store_prefix())
