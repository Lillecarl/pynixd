"""Run the benchmarks against both event loops.

The suite-wide `anyio_backend` pins uvloop, so every benchmark figure recorded
before this file was a uvloop figure and said so nowhere. A throughput or
latency claim about pynixd is a claim about the loop underneath it, and the two
loops do not schedule the same way -- uvloop is libuv in C, asyncio is Python.

Parametrised at session scope, which is the scope the shared fixture uses, so
`pynixd_server` is rebuilt once per backend rather than shared across both. The
ids are what the log lines and the test names carry, so a number can be
attributed to a loop afterwards.

anyio stays the API on top of both: this selects asyncio's loop implementation,
it does not change a single `await` in pynixd.
"""

from __future__ import annotations

import pytest


@pytest.fixture(
    scope="session",
    params=[
        pytest.param(("asyncio", {"use_uvloop": False}), id="asyncio"),
        pytest.param(("asyncio", {"use_uvloop": True}), id="uvloop"),
    ],
)
def anyio_backend(request: pytest.FixtureRequest) -> tuple[str, dict[str, bool]]:
    """Both loops, one parameter each."""
    return request.param  # type: ignore[no-any-return]
