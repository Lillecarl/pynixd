"""Shared utility functions."""

from __future__ import annotations

import hashlib
import os

NIX32_CHARS = "0123456789abcdfghijklmnpqrsvwxyz"


def nix32_encode(data: bytes) -> str:
    """Encode bytes to Nix's base-32 format.

    Nix uses a custom base-32 encoding (5 bits per character, LSB first)
    with the alphabet ``0123456789abcdfghijklmnpqrsvwxyz``.
    """
    if not data:
        return ""
    size = len(data)
    result_len = (size * 8 - 1) // 5 + 1
    result: list[str] = []
    for n in range(result_len - 1, -1, -1):
        b = n * 5
        i = b // 8
        j = b % 8
        c = (data[i] >> j) & 0x1F
        if i + 1 < size:
            c |= (data[i + 1] << (8 - j)) & 0x1F
        result.append(NIX32_CHARS[c])
    return "".join(result)


def compress_hash(data: bytes, new_size: int) -> bytes:
    """XOR-fold a hash digest into *new_size* bytes.

    Matches Nix's ``hash::compressHash`` (used by ``Store::makeStorePath``).
    """
    if len(data) == new_size:
        return data
    assert len(data) > new_size
    result = bytearray(data[:new_size])
    for i in range(new_size, len(data)):
        result[i % new_size] ^= data[i]
    return bytes(result)


def random_nix32_hash() -> str:
    """Generate a random 32-char nix base32 hash for probe derivations."""
    return nix32_encode(hashlib.sha256(os.urandom(32)).digest())[:32]
