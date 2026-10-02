"""`ResourceGate.wait_mem_clear` answers a clear gate without scaffolding.

A set flag returns at once; a pressured gate still waits for the release
or the timeout. These pin the three answers, not the speed: the benchmark
measures that.
"""

from __future__ import annotations

import anyio
import pytest

from pynixd.monitor import ResourceExhaustedError, ResourceGate


async def test_a_clear_gate_returns() -> None:
    """No waiting, no timeout, no error."""
    gate = ResourceGate()
    gate.mem_clear.set()

    await gate.wait_mem_clear(timeout=5.0)


async def test_a_pressured_gate_waits_for_the_release() -> None:
    """`clear()` holds waiters; `set()` lets this one through."""
    gate = ResourceGate()
    gate.mem_clear.clear()

    async def release() -> None:
        await anyio.sleep(0.05)
        gate.mem_clear.set()

    async with anyio.create_task_group() as group:
        group.start_soon(release)
        await gate.wait_mem_clear(timeout=5.0)


async def test_a_gate_that_never_clears_times_out() -> None:
    """Pressure past the timeout is an error, not a hang."""
    gate = ResourceGate()
    gate.mem_clear.clear()

    with pytest.raises(ResourceExhaustedError):
        await gate.wait_mem_clear(timeout=0.05)
