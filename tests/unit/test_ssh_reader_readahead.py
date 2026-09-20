"""`SSHNixReader` reads ahead. Prove it never strands a byte.

The reader buffers, so a read of 8 bytes may pull 256 KiB off the stream. That
is the whole point -- the NAR forward spends 2.47s of 2.99s in asyncssh, two
calls per frame -- and it is also the way it could corrupt a connection: bytes
held in this buffer belong to whatever reads next, and a framed operation
followed by a raw one is where that shows.

These drive `SSHNixReader` itself over a stub stream, because the property is
about the reader's own bookkeeping and not about SSH.
"""

from __future__ import annotations

import pytest

from pynixd.wire import FramedReader, SSHNixReader


class _StubStream:
    """The `read`/`readexactly` pair asyncssh gives, over a fixed buffer.

    `read` returns less than asked for on purpose. A stream that always
    returned the full amount would hide a reader that assumes it does.
    """

    def __init__(self, data: bytes, max_read: int = 4096) -> None:
        self._data = data
        self._pos = 0
        self._max_read = max_read

    async def read(self, n: int) -> bytes:
        out = self._data[self._pos : self._pos + min(n, self._max_read)]
        self._pos += len(out)
        return out

    async def readexactly(self, n: int) -> bytes:
        if self._pos + n > len(self._data):
            raise EOFError
        out = self._data[self._pos : self._pos + n]
        self._pos += n
        return out


def _framed(payload: bytes, frame_size: int) -> bytes:
    out = bytearray()
    for off in range(0, len(payload), frame_size):
        chunk = payload[off : off + frame_size]
        out.extend(len(chunk).to_bytes(8, "little"))
        out.extend(chunk)
    out.extend((0).to_bytes(8, "little"))
    return bytes(out)


async def test_a_raw_read_after_a_framed_one_sees_every_byte() -> None:
    """The case read-ahead could break: framed stream, then a raw tail.

    The framed reader stops at the terminator. Whatever the SSH reader pulled
    past it is still owed to the next read, and that read goes through the same
    `SSHNixReader`. If the buffer were skipped, the tail would come back short
    or shifted.
    """
    payload = bytes(range(256)) * 400
    tail = b"the-next-operation-reads-this"
    src = SSHNixReader(_StubStream(_framed(payload, 4096) + tail))  # type: ignore[arg-type]

    framed = FramedReader(src)
    got = b""
    while len(got) < len(payload):
        got += await framed.readexactly(min(8192, len(payload) - len(got)))

    assert got == payload
    await framed.ensure_eof()

    # The raw tail, through the same reader that did the read-ahead.
    assert await src.readexactly(len(tail)) == tail


async def test_a_short_read_does_not_lose_the_remainder() -> None:
    """A read smaller than the stream leaves the rest readable, in order."""
    data = bytes(range(256)) * 64
    src = SSHNixReader(_StubStream(data))  # type: ignore[arg-type]

    first = await src.readexactly(8)
    rest = b""
    while len(rest) < len(data) - 8:
        rest += await src.readexactly(min(1000, len(data) - 8 - len(rest)))

    assert first + rest == data


async def test_a_read_larger_than_the_readahead_still_joins_the_buffer() -> None:
    """A bulk read bypasses buffering, so it must still take what is buffered.

    Reading one byte fills the buffer. The next read is larger than
    `_READAHEAD`, so it goes straight to the stream -- and it has to start with
    the bytes already held, or it silently skips them.
    """
    data = bytes(range(256)) * 4096
    # `max_read` stays small on purpose. A large one buffers so much that the
    # first read satisfies the second from memory, and the bulk branch this
    # test exists for never runs -- which is how an earlier version of this
    # test passed against a reader that dropped the buffered head.
    src = SSHNixReader(_StubStream(data, max_read=4096))  # type: ignore[arg-type]

    # Twice the read-ahead, not one byte over it. The buffered head is
    # subtracted before the branch is chosen, so `_READAHEAD + 1` still lands
    # in the buffered path and tests nothing.
    bulk_size = SSHNixReader._READAHEAD * 2

    first = await src.readexactly(1)
    bulk = await src.readexactly(bulk_size)

    assert first + bulk == data[: 1 + bulk_size]


async def test_the_reader_reports_dirty_while_it_holds_bytes() -> None:
    """`ensure_eof` and the connection pool ask this. It must count the buffer.

    A reader that read ahead and reported clean would let a caller conclude the
    stream was drained while bytes were still owed.
    """
    src = SSHNixReader(_StubStream(bytes(range(256)) * 64))  # type: ignore[arg-type]

    await src.readexactly(8)
    assert await src.is_dirty()

    with pytest.raises(EOFError):
        while True:
            await src.readexactly(4096)
