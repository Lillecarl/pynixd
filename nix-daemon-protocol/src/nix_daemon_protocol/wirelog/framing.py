"""The file that holds one recorded connection.

A recording is a list of chunks. Each chunk is the bytes of one read, the
direction those bytes went, and the time they arrived. The recorder writes it,
and `decode` reads it.

The format keeps the two directions apart and keeps the order between them,
because a decoder needs both. It states the length of each chunk, so a reader
finds the end of a recording that a killed run cut short.

    magic     b"NIXWIRE2"
    storedir  length    (2 bytes) little-endian
              path      (length bytes) UTF-8
    chunk     direction (1 byte)  b"C" or b"S"
              nanos     (8 bytes) little-endian, from the start of the file
              length    (8 bytes) little-endian
              payload   (length bytes)

**The store directory belongs to the recording, not to the reader.** Nix's
functional suite gives every test its own store, so a path on the wire there
begins with that test's directory and not with `/nix/store`. A reader that
asks its own process instead refuses every path in the file and reports each
test as a difference. Issue #37.

`NIXWIRE1` is the same file without the store directory. It still reads, and
`Recording.store_dir` is then `None`, which means "whatever the reader has".

A chunk holds the bytes of one read, and a read of a socket says nothing about
where a protocol message starts or ends. `decode` finds the messages, and this
module does not. That division is the point: the recorder writes this file and
knows no protocol, so a defect in the codecs of this package cannot change
what a recording holds.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import BinaryIO

MAGIC = b"NIXWIRE2"
LEGACY_MAGIC = b"NIXWIRE1"
HEADER = struct.Struct("<cQQ")
STORE_DIR_HEADER = struct.Struct("<H")


class Direction(Enum):
    """Which way the bytes of a chunk went."""

    CLIENT = b"C"
    """From the client to the daemon. A request."""

    SERVER = b"S"
    """From the daemon to the client. A log message, or a response."""


@dataclass(frozen=True)
class Chunk:
    """The bytes of one read, and where they came from."""

    direction: Direction
    nanos: int
    data: bytes


@dataclass(frozen=True)
class Recording:
    """A whole recording: the store its paths belong to, and its chunks."""

    store_dir: str | None
    chunks: list[Chunk]


def encode_header(store_dir: str) -> bytes:
    """The first bytes of a recording, which name the store of its paths."""
    encoded = store_dir.encode()
    return MAGIC + STORE_DIR_HEADER.pack(len(encoded)) + encoded


def write_magic(handle: BinaryIO, store_dir: str) -> None:
    """Write the first bytes of a recording."""
    handle.write(encode_header(store_dir))


def encode_chunk(direction: Direction, nanos: int, data: bytes) -> bytes:
    """One chunk, ready to append to a recording."""
    return HEADER.pack(direction.value, nanos, len(data)) + data


def read_chunks(path: Path | str) -> list[Chunk]:
    """Every whole chunk of a recording, in the order it was written.

    A recording that ends in the middle of a chunk gives up the last chunk and
    keeps the rest. A killed run leaves such a file, and the chunks before the
    cut still say what happened.
    """
    return read_recording(path).chunks


def read_recording(path: Path | str) -> Recording:
    """A recording's store directory and every whole chunk of it.

    A recording that ends in the middle of a chunk gives up the last chunk and
    keeps the rest. A killed run leaves such a file, and the chunks before the
    cut still say what happened.
    """
    raw = Path(path).read_bytes()
    store_dir: str | None = None
    if raw.startswith(MAGIC):
        offset = len(MAGIC)
        (length,) = STORE_DIR_HEADER.unpack_from(raw, offset)
        offset += STORE_DIR_HEADER.size
        store_dir = raw[offset : offset + length].decode()
        offset += length
    elif raw.startswith(LEGACY_MAGIC):
        offset = len(LEGACY_MAGIC)
    else:
        raise ValueError(f"{path} is not a recording: it does not start with {MAGIC!r}")

    chunks: list[Chunk] = []
    while offset + HEADER.size <= len(raw):
        marker, nanos, length = HEADER.unpack_from(raw, offset)
        end = offset + HEADER.size + length
        if end > len(raw):
            break  # The run stopped in the middle of this chunk.
        chunks.append(Chunk(Direction(marker), nanos, raw[offset + HEADER.size : end]))
        offset = end
    return Recording(store_dir=store_dir, chunks=chunks)


def one_direction(chunks: list[Chunk], direction: Direction) -> bytes:
    """Every byte that went one way, joined in order.

    This is the stream that a reader of the protocol sees. The chunks are the
    reads of a socket, and a protocol message can lie across two of them.
    """
    return b"".join(chunk.data for chunk in chunks if chunk.direction is direction)
