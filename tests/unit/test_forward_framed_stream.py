"""A NAR forward must suspend and must not buffer the whole payload.

Both directions. `forward_framed` receives, `forward_raw` serves.

`forward_framed` moves a framed NAR from the client to the daemon for
AddToStore (op 7) and AddToStoreNar (op 39). `nix copy --to ssh-ng://` does not
reach it -- that is AddMultipleToStore (op 44) at protocol 1.32 and above,
`remote-store.cc:508`. `nix store add-path`, `nix-store --import` and any
client below 1.32 do, `remote-store.cc:451`.

Nix frames with `FramedSink`, a `BufferedSink` of 32 KiB
(`serialise.hh:71,724`). A 2.8 GiB closure is therefore about 91750 turns of
this loop, and each turn must give the event loop a chance to run something
else.

Two faults, one per test, and they are separate:

- No `drain` inside the loop. `write` hands the bytes to the transport and
  returns, so the transport holds everything the daemon has not taken yet.
- No suspension. A buffered read returns without reaching the loop, and `drain`
  returns without reaching it while the transport stays below its high-water
  mark. So the loop can run to the end of a NAR having scheduled nothing --
  including accepting the connection a TCP liveness probe opens. `/healthz`
  fails at `health_loop_lag_max` seconds of measured lag.

`forward_raw` carries NarFromPath (op 38), which is a node pulling from
pynixd rather than pushing to it. Its source is the local daemon over a Unix
socket, so it suspends even less often than the receiving direction does.

The doubles here suspend for nothing, which is the worst case rather than an
unfair one: `SSHNixReader` reads 256 KiB ahead and serves eight 32 KiB frames
per socket read, and asyncio's `drain` returns without yielding below the
high-water mark.

`tests/benchmark/test_bench_nar_profile.py` measures the same two numbers
against a real client. This one asserts them, in milliseconds, with no
hardware in the result.
"""

from __future__ import annotations

import anyio

from pynixd.wire import BytesReader, BytesWriter, NixWriter, forward_framed, forward_raw

_FRAME = 32 * 1024
_FRAMES = 512
# `forward_raw`'s own chunk, so the two shapes move the same 16 MiB.
_CHUNK = 1024 * 1024
_CHUNKS = 16


def _framed_payload(frames: int = _FRAMES, size: int = _FRAME) -> bytes:
    """`frames` chunks of `size`, then the zero terminator Nix's FramedSink writes."""
    out = BytesWriter()
    for i in range(frames):
        out.write_uint64(size)
        out.write(bytes([i % 256]) * size)
    out.write_uint64(0)
    return out.get_bytes()


class _TransportWriter(NixWriter):
    """A writer that holds every byte until `drain`, and records the high mark.

    This is what an asyncio transport does. `write` appends to the buffer and
    returns; the buffer empties when the peer takes the bytes, which is what
    `drain` waits for. `drain` does not suspend here, because a real one does
    not suspend below the high-water mark either -- that is the whole reason
    the `checkpoint` in the loop is a separate fix from the `drain`.
    """

    def __init__(self) -> None:
        super().__init__(identifier="transport-double")
        self.buffered = 0
        self.peak = 0
        self.sent = 0
        self.drains = 0

    def write(self, data: bytes) -> None:
        self.buffered += len(data)
        self.sent += len(data)
        self.peak = max(self.peak, self.buffered)

    async def drain(self) -> None:
        self.drains += 1
        self.buffered = 0


class _Ticker:
    """Count how often the event loop gets to run something else."""

    def __init__(self) -> None:
        self.ticks = 0
        self._stop = False

    async def run(self) -> None:
        while not self._stop:
            self.ticks += 1
            await anyio.sleep(0)

    def stop(self) -> None:
        self._stop = True


async def test_forward_framed_lets_the_loop_run() -> None:
    """The loop schedules the rest of the process while a NAR moves through it.

    One tick per frame is the floor a `checkpoint` per frame gives. Fewer than
    that means a probe, a metrics scrape or a second client waits for the whole
    payload.
    """
    src = BytesReader(_framed_payload())
    dst = _TransportWriter()
    ticker = _Ticker()

    async with anyio.create_task_group() as tg:
        tg.start_soon(ticker.run)
        await forward_framed(src, dst)
        ticker.stop()

    assert dst.sent == _FRAMES * (_FRAME + 8) + 8
    assert ticker.ticks >= _FRAMES, f"the loop ran {ticker.ticks} times for {_FRAMES} frames"


async def test_forward_framed_does_not_buffer_the_payload() -> None:
    """The transport holds about one frame, not the closure.

    A push of 2.8 GiB against a 64Mi request is an OOM kill, and the container
    restart destroys the evidence that it was a transfer.
    """
    src = BytesReader(_framed_payload())
    dst = _TransportWriter()

    await forward_framed(src, dst)

    # Four frames of slack: the loop writes a length and a payload per turn,
    # and a fix that drains every few frames is as correct as one that drains
    # every frame. The whole payload is 512 frames.
    assert dst.peak <= 4 * (_FRAME + 8), f"peak {dst.peak} of {dst.sent} bytes held"


async def test_forward_raw_lets_the_loop_run() -> None:
    """The serving direction, which is the one a node's pull takes.

    `forward_raw` carries NarFromPath (op 38). It reads from the local daemon
    over a Unix socket, which is ready almost every time it is asked, so the
    read suspends even less often than op 39's read from the network.
    """
    src = BytesReader(b"\0" * (_CHUNK * _CHUNKS))
    dst = _TransportWriter()
    ticker = _Ticker()

    async with anyio.create_task_group() as tg:
        tg.start_soon(ticker.run)
        await forward_raw(src, dst, _CHUNK * _CHUNKS, chunk_size=_CHUNK)
        ticker.stop()

    assert dst.sent == _CHUNK * _CHUNKS
    assert ticker.ticks >= _CHUNKS, f"the loop ran {ticker.ticks} times for {_CHUNKS} chunks"
    assert dst.peak <= 2 * _CHUNK, f"peak {dst.peak} of {dst.sent} bytes held"
