"""The queue gauge counts the queue, so it cannot drift. Issue #89.

The gauge moved by increments. A build that failed after its task ended
counted as pending, and a retry took a second pending decrement, so dynhetz
read `pynixd_build_queue_size{status="pending"} -2` and more builds running
than there were store connections.
"""

from __future__ import annotations

import anyio
from prometheus_client import REGISTRY

from pynixd import metrics
from pynixd.build_queue import BuildQueue
from pynixd.serde import BasicDerivation, BuildDerivationRequest, BuildMode, StorePath


def _request(name: str) -> BuildDerivationRequest:
    return BuildDerivationRequest(
        drv_path=StorePath(path=f"/nix/store/{name * 32}-{name}.drv"),
        derivation=BasicDerivation(platform="x86_64-linux", builder=""),
        build_mode=BuildMode.NORMAL,
    )


def _gauge(status: str) -> float | None:
    return REGISTRY.get_sample_value("pynixd_build_queue_size", {"status": status})


async def test_a_build_that_fails_after_its_task_ended_moves_no_gauge_wrongly() -> None:
    queue = BuildQueue()
    metrics.watch_queue(queue)
    await queue.enqueue(_request("a"))
    running_id, _ = await queue.enqueue(_request("b"))
    ended_id, _ = await queue.enqueue(_request("c"))

    queue.by_id[running_id].worker = anyio.CancelScope()
    # The worker of this one already ended, so no handle is left.
    await queue.fail(ended_id, "the store went away")

    assert (_gauge("pending"), _gauge("building"), _gauge("done")) == (1.0, 1.0, 1.0)
