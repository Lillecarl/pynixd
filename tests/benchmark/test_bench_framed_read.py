"""Compare candidate read paths for FramedReader.

`test_bench_nar_profile.py` attributed 97% of the NAR forward's CPU to
`FramedReader.readexactly`, which copies each byte three to four times. This
measures the candidates against each other before one of them ships.

The source here is `BytesReader`, in memory. That is deliberate: it takes the
socket out, so what is left is the copying. The cost of `read_uint64` taking a
frame prefix as its own socket read does not appear here at all -- it cannot,
with no socket -- and is measured end to end by the other file instead.

The baseline variant is the shipped `FramedReader`, not a copy of it. Each
candidate subclasses it and overrides only what it changes, so a difference in
the numbers is a difference in that override.

**What this measured, and why the copying is not the fault.**

    variant       frame   cpu_s   ms/MiB  peak_MiB  peak/payload
    baseline        64K   0.046     0.72      4.14         0.065
    bulk-bypass     64K   0.062    0.961    268.81         4.200
    baseline      1024K   0.019     0.30      5.01         0.078
    bulk-bypass   1024K   0.008    0.131      2.01         0.031

`bulk-bypass` wins only where a frame happens to equal the read size. A client
frames at 64 KiB, and there it costs more CPU than the baseline and holds four
times the payload.

The number that matters is the comparison against the real transfer: copying
costs 0.72 ms/MiB here and the transfer costs 24 ms/MiB, so the copying is
about 3% of it. The rest is per-call asyncssh overhead -- `readexactly` on the
socket, and `read_uint64` taking each frame prefix as a read of its own. Buffer
the socket, not the accumulator.
"""

from __future__ import annotations

import resource
import time
import tracemalloc
from typing import TYPE_CHECKING

import pytest
import structlog

from pynixd.wire import BytesReader, FramedReader

if TYPE_CHECKING:
    from pynixd.wire import NixReader

log = structlog.get_logger(__name__)

_MIB = 1024 * 1024

# The client frames at its own size, not at ours, so the interesting axis is
# how many frames a 1 MiB read spans. 64 KiB frames mean sixteen.
_FRAME_SIZES = [64 * 1024, 1024 * 1024]
_PAYLOAD_MIB = 64
_READ_SIZE = 1024 * 1024


class ViewOut(FramedReader):
    """Hand the payload out as a view, and never compact.

    Two of the copies go: the slice and the `bytes()` around it. `_compact`'s
    `del _buf[:pos]` goes with them, because a view handed out earlier would
    see the bytes move under it.

    The accumulator therefore grows for the life of the stream, which is the
    trade this variant exists to price.
    """

    async def readexactly(self, n: int) -> memoryview:  # type: ignore[override]
        while (len(self._buf) - self._pos) < n:
            if self._eof:
                raise EOFError("Framed stream ended")
            size = await self._src.read_uint64()
            if size == 0:
                self._eof = True
                break
            self._buf.extend(await self._src.readexactly(size))
        result = memoryview(self._buf)[self._pos : self._pos + n]
        self._pos += n
        return result


class BulkBypass(FramedReader):
    """Return a whole frame directly when it satisfies the request.

    The accumulator is untouched on the bulk path, so no byte is copied into
    it and none is copied out. A request that does not line up with a frame
    falls back to the inherited implementation.
    """

    async def readexactly(self, n: int) -> bytes:
        if (len(self._buf) - self._pos) == 0 and not self._eof:
            size = await self._src.read_uint64()
            if size == 0:
                self._eof = True
                raise EOFError("Framed stream ended")
            data = await self._src.readexactly(size)
            if size == n:
                return data
            self._buf.extend(data)
        return await super().readexactly(n)


def _framed(payload: bytes, frame_size: int) -> bytes:
    """Frame a payload the way a client writes it: [uint64 size][data]...[0]."""
    out = bytearray()
    for off in range(0, len(payload), frame_size):
        chunk = payload[off : off + frame_size]
        out.extend(len(chunk).to_bytes(8, "little"))
        out.extend(chunk)
    out.extend((0).to_bytes(8, "little"))
    return bytes(out)


async def _drain(reader: NixReader, total: int, read_size: int) -> int:
    got = 0
    while got < total:
        want = min(read_size, total - got)
        data = await reader.readexactly(want)
        got += len(data)
    return got


async def test_a_view_over_the_accumulator_blocks_the_next_frame() -> None:
    """A handed-out memoryview stops the accumulator growing. Measured, not assumed.

    This is why `readexactly` copies out instead of returning a view. CPython
    refuses to resize a `bytearray` while an export of it is alive, so the view
    from one read blocks the `extend` of the next:

        BufferError: Existing exports of data: object cannot be re-sized

    Returning a view needs a buffer that never resizes, or a contract that the
    caller releases before reading again. Both are larger changes than the
    copying is worth -- copying measures 0.72 ms/MiB here against 24 ms/MiB for
    the real transfer, so it is about 3% of the cost.
    """
    payload = bytes(4 * _MIB)
    reader = ViewOut(BytesReader(_framed(payload, 64 * 1024)))

    with pytest.raises(BufferError, match="cannot be re-sized"):
        await _drain(reader, len(payload), _READ_SIZE)


@pytest.mark.benchmark
@pytest.mark.parametrize("frame_size", _FRAME_SIZES)
@pytest.mark.parametrize("variant", ["baseline", "bulk-bypass"])
async def test_bench_framed_read_variants(variant: str, frame_size: int) -> None:
    """Measure CPU and peak allocation for one read path over a fixed payload."""
    payload = bytes(_PAYLOAD_MIB * _MIB)
    wire_bytes = _framed(payload, frame_size)

    src = BytesReader(wire_bytes)
    reader: NixReader = FramedReader(src) if variant == "baseline" else BulkBypass(src)

    tracemalloc.start()
    tracemalloc.reset_peak()
    r0 = resource.getrusage(resource.RUSAGE_SELF)
    t0 = time.perf_counter()

    got = await _drain(reader, len(payload), _READ_SIZE)

    wall = time.perf_counter() - t0
    r1 = resource.getrusage(resource.RUSAGE_SELF)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert got == len(payload)

    cpu = (r1.ru_utime - r0.ru_utime) + (r1.ru_stime - r0.ru_stime)
    log.info(
        "bench_framed_read_variant",
        variant=variant,
        frame_kib=frame_size // 1024,
        payload_mib=_PAYLOAD_MIB,
        wall_s=round(wall, 3),
        cpu_s=round(cpu, 3),
        cpu_ms_per_mib=round(cpu * 1000 / _PAYLOAD_MIB, 3),
        peak_mib=round(peak / _MIB, 2),
        peak_over_payload=round(peak / len(payload), 3),
    )
