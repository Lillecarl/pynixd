"""The watchdog that catches a stall in the act.

A push on nixlab2 sat at 100% CPU for about ten minutes with no progress, then
resumed. Nothing recorded what it was doing: a probe restarts the container,
the restart clears the lifetime-max gauge, and no benchmark here has ever
reproduced it. The transfer fixes cannot be the cause either, because only
42.9 MiB moved in total -- the cost is not proportional to the bytes.

So the evidence has to be taken from inside the process while it is stuck.
The last test is the one that matters: it stalls a real event loop with a
real CPU-bound Python loop and asserts a dump came out. Everything the
watchdog claims rests on the GIL being released during that stall, and only
running it proves that.
"""

from __future__ import annotations

import asyncio
import io
import threading
import time

from pynixd.health import LoopLagMonitor, StallWatchdog


def test_a_beating_loop_is_never_dumped() -> None:
    dog = StallWatchdog(threshold=1.0, stream=io.StringIO())
    now = time.monotonic()
    dog.beat()

    assert dog._check(now + 0.5) is False
    assert dog.dumps == 0


def test_a_stalled_loop_is_dumped_once() -> None:
    """One dump per stall. A ten-minute stall must not write hundreds."""
    out = io.StringIO()
    dog = StallWatchdog(threshold=1.0, stream=out)
    dog.beat()
    now = time.monotonic()

    assert dog._check(now + 2.0) is True
    dog.dump()
    # Still stalled, still past the threshold, and already reported.
    assert dog._check(now + 3.0) is False
    assert dog._check(now + 600.0) is False

    dog.beat()
    assert dog._check(time.monotonic() + 2.0) is True


def test_zero_disables_it() -> None:
    dog = StallWatchdog(threshold=0.0, stream=io.StringIO())
    dog.beat()
    assert dog._check(time.monotonic() + 3600.0) is False


def test_the_dump_names_the_stalled_thread() -> None:
    out = io.StringIO()
    dog = StallWatchdog(threshold=0.01, stream=out)
    dog.dump()
    text = out.getvalue()

    assert "has not run a callback" in text
    # faulthandler's own output, which is what carries the diagnosis.
    assert "Thread" in text or "File " in text


async def test_it_fires_against_a_real_cpu_bound_stall() -> None:
    """The claim that matters, run rather than argued.

    The watchdog is a thread, and a thread only helps if it is scheduled while
    the loop is not. CPU-bound Python releases the GIL every switch interval,
    so it is -- but that is the whole premise, and a stall inside a C call that
    holds the GIL would defeat it. This is the negative control for the
    premise.
    """
    out = io.StringIO()
    dog = StallWatchdog(threshold=0.2, interval=0.05, stream=out)
    monitor = LoopLagMonitor(interval=0.02, watchdog=dog)
    dog.beat()
    thread = dog.start()
    task = asyncio.create_task(monitor.run())
    try:
        await monitor.wait_for_sample()
        assert dog.dumps == 0, "a loop that is running must not be dumped"

        # Block the loop the way the reported failure does: Python, on the
        # loop, without awaiting.
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            sum(range(2000))
    finally:
        monitor.stop()
        dog.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        thread.join(timeout=5)

    assert dog.dumps >= 1, "a one second CPU-bound stall produced no dump"
    assert "has not run a callback" in out.getvalue()
    assert threading.current_thread() is threading.main_thread()
