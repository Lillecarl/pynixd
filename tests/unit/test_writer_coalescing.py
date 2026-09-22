"""A response carrying many values reaches the transport as a few writes.

asyncio charges per `transport.write`, not per byte, and the charge is the
whole buffer: `write` calls `_maybe_pause_protocol`, which calls
`get_write_buffer_size`, which is `sum(map(len, self._buffer))` over
everything not yet on the socket. `NixWriter.write_bytes` is two or three
writes per value, so an op carrying thousands of store paths paid that sum
tens of thousands of times over a buffer that only grew. The cost was
quadratic in the number of values and none of it suspended, so the event
loop ran nothing for the whole message.

Measured before the fix, one QueryValidPaths over a Unix socket: 4000 paths
stalled the loop 0.82s, 8000 stalled it 3.28s. Doubling the count quadrupled
the stall. A liveness probe times out at 10s, so `/healthz` stopped
answering at all rather than answering unhealthy.

This asserts the count and not the time, so there is no hardware in the
result. The count is what the quadratic was in.
"""

from __future__ import annotations

import anyio

from pynixd.wire import NixWriter

_PATH = "/nix/store/00000000000000000000000000000000-some-package-name-1.2.3"
_COUNT = 4000


class _CountingWriter(NixWriter):
    """Records what reaches the transport, which is what asyncio charges for."""

    def __init__(self) -> None:
        super().__init__(identifier="counting-double")
        self.writes: list[int] = []
        self.drains = 0

    def _write_to_transport(self, data: bytes) -> None:
        self.writes.append(len(data))

    async def _drain_transport(self) -> None:
        self.drains += 1


async def test_many_strings_reach_the_transport_in_few_writes() -> None:
    w = _CountingWriter()

    w.write_uint64(_COUNT)
    for i in range(_COUNT):
        w.write_string(f"{_PATH}-{i}")
    await w.drain()

    total = sum(w.writes)
    # Unbuffered this was two or three writes per string. One flush per
    # 64 KiB plus the last one on `drain` is the whole budget.
    budget = total // NixWriter._FLUSH_BYTES + 2
    assert len(w.writes) <= budget, f"{len(w.writes)} writes for {total} bytes, budget {budget}"
    assert w.drains == 1


async def test_drain_flushes_what_is_pending() -> None:
    """Nothing may be left in the buffer once a message is drained.

    The op loop of `DaemonProxy` and `Connection.call` both drain after a
    complete message, and that is what makes buffering safe: a byte held
    back here would otherwise never be sent.
    """
    w = _CountingWriter()

    w.write_string("small")
    assert w.writes == [], "a short write should not reach the transport yet"
    assert await w.is_dirty(), "a pending byte is in flight and must say so"

    await w.drain()
    assert sum(w.writes) > 0
    assert not await w.is_dirty()


def test_bytes_writer_does_not_use_the_buffer() -> None:
    """`BytesWriter` overrides `write`, so it never reaches the coalescing path.

    Named because the fix lives in the base class: a subclass that writes
    somewhere other than a transport must keep its own behaviour.
    """
    from pynixd.wire import BytesWriter

    b = BytesWriter()
    b.write_string("hello")
    assert b.get_bytes()
    assert b._pending == bytearray()


if __name__ == "__main__":
    anyio.run(test_many_strings_reach_the_transport_in_few_writes)
