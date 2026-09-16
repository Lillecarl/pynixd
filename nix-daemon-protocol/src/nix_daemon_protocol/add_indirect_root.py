"""AddIndirectRoot operation — WireRequest/WireResponse types."""

from __future__ import annotations

from typing import ClassVar

from .wire_ops import WireRequest, WireResponse


class AddIndirectRootResponse(WireResponse):
    """AddIndirectRoot response — single uint64 value."""

    value: int


class AddIndirectRootRequest(WireRequest):
    """AddIndirectRoot request — one file system path on the wire.

    **Not a `StorePath`.** An indirect root is the symlink that `nix build`
    leaves behind, and it lives outside the store:
    `/tmp/nix-build-<pid>-<n>/result`. Nix takes it as a plain path --
    `IndirectRootStore::addIndirectRoot(const std::filesystem::path &)`,
    `src/libstore/include/nix/store/indirect-root-store.hh:73` -- and
    `LocalStore::addIndirectRoot` at `src/libstore/gc.cc:47` is what reads it.

    It was declared `StorePath` and nothing complained, because that class
    was a `str` subclass that validated nothing. The class now refuses an
    absolute path of another store, which is what found this.
    """

    op: ClassVar[int] = 12
    response_type = AddIndirectRootResponse
    path: str
