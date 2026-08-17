"""The two store directories of the store that this process serves.

Nix keeps two, and so does this module. `LocalFSStore` of Nix names them
`storeDir` and `realStoreDir`, and they are not always the same directory:

- **The store directory is the one in a store path.** It is what
  `builtins.storeDir` answers, and it is the text in front of the hash of
  every path on the wire. `NIX_STORE_DIR` sets it, and the default is
  `/nix/store`.
- **The real store directory is where the files are.** `--store <root>` of Nix
  puts them at `<root>/nix/store`, and it changes no store path. So a chroot
  store answers `/nix/store/abc-foo` and keeps that path at
  `<root>/nix/store/abc-foo`.

The two are equal in the usual case, and `real_store_dir()` answers
`store_dir()` until something sets it.

**Read the wire with `store_dir`, and read the file system with
`real_store_dir`.** A confusion of the two made pynixd send a derivation to
the daemon with an incomplete `inputSrcs`, because it could not find the input
`.drv` files on disk. The daemon then found no reference in the output it
built, and registered a path that names none of the paths it needs.

One value of each for the whole process, as `settings.nixStore` is in Nix. The
client and the daemon exchange a whole path and the handshake does not
negotiate the part in front of the hash, so a client of a store at another
directory gets an error and not a translation.

This module holds the values, and not `pynixd`, because `nix_daemon_protocol`
decodes a store path and must not import `pynixd`.
"""

from __future__ import annotations

import os

DEFAULT_STORE_DIR = "/nix/store"

_store_dir: str | None = None
_real_store_dir: str | None = None


def _absolute(path: str | os.PathLike[str], what: str) -> str:
    value = str(path).rstrip("/")
    if not value.startswith("/"):
        raise ValueError(f"the {what} must be an absolute path: {path!r}")
    return value


def store_dir() -> str:
    """The directory in a store path, with no separator at the end."""
    global _store_dir
    if _store_dir is None:
        _store_dir = os.environ.get("NIX_STORE_DIR", "").rstrip("/") or DEFAULT_STORE_DIR
    return _store_dir


def store_prefix() -> str:
    """The store directory with a separator at the end."""
    return store_dir() + "/"


def real_store_dir() -> str:
    """The directory on the file system that holds the store paths."""
    if _real_store_dir is None:
        return store_dir()
    return _real_store_dir


def set_store_dir(path: str | os.PathLike[str]) -> None:
    """Give the process the directory that a store path names.

    `NIX_STORE_DIR` answers this already, so few callers need it.
    """
    global _store_dir
    _store_dir = _absolute(path, "store directory")


def set_real_store_dir(path: str | os.PathLike[str]) -> None:
    """Give the process the directory that holds the files of the store.

    The daemon calls this once, before it accepts a connection, with
    `<root>/nix/store` of the store that it serves.
    """
    global _real_store_dir
    _real_store_dir = _absolute(path, "real store directory")


def reset_store_dir() -> None:
    """Forget both values, so that the next reader finds them again.

    A test uses this. Production code calls the two setters instead.
    """
    global _store_dir, _real_store_dir
    _store_dir = None
    _real_store_dir = None


def in_store_dir(path: str) -> bool:
    """True when *path* is a path inside the store directory."""
    return path.startswith(store_prefix())


def on_disk(path: str) -> str:
    """Where *path*, which is a whole store path, is on the file system.

    A chroot store answers `/nix/store/abc-foo` and keeps the file at
    `<root>/nix/store/abc-foo`. This turns the first into the second, and
    leaves a path of a store that is not a chroot store alone.
    """
    prefix = store_prefix()
    if not path.startswith(prefix):
        raise ValueError(f"{path!r} is not a path of the store at {store_dir()!r}")
    return f"{real_store_dir()}/{path[len(prefix) :]}"
