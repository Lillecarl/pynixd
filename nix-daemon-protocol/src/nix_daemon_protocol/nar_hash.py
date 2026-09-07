"""NARHash — base16-encoded NAR SHA256 hash as a WireString."""

from __future__ import annotations

from .wire_scalar import WireScalar

# What `Hash::to_string(HashFormat::Base16, true)` of Nix puts in front of the
# digest. The database of a local store holds that form, at
# `local-store.cc:677`, and the wire does not.
ALGORITHMS = ("blake3", "md5", "sha1", "sha256", "sha512")


class NARHash(WireScalar):
    """Base16-encoded NAR SHA256 hash — no algorithm prefix on wire.

    `WorkerProto::Serialise<UnkeyedValidPathInfo>::write` at
    `worker-protocol.cc:356` of Nix writes
    `narHash.to_string(HashFormat::Base16, false)`, and `false` is what leaves
    the name of the algorithm out.

    **A source of this value may still carry the name, and this takes it
    off.** `LocalStore` of Nix writes `sha256:<digest>` into the `narHash`
    column of its database, so a fast path that reads that column and answers
    a client sends a string that `nix-daemon` never sends. The client accepts
    it, because `Hash::parseAny` at `worker-protocol.cc:339` reads both forms,
    so nothing failed and the two answers differed. `tests/parity` of pynixd
    is what saw it.
    """

    def __new__(cls, value: str = "", *, hash: str | None = None) -> NARHash:  # noqa: A002
        if hash is not None:
            if value and value != hash:
                raise ValueError("NARHash value and hash disagree")
            value = hash
        head, separator, digest = value.partition(":")
        if separator and head in ALGORITHMS:
            value = digest
        return super().__new__(cls, value)

    @property
    def hash(self) -> str:
        """Compatibility spelling for the canonical string value."""
        return self
