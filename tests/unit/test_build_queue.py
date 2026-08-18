"""Unit tests for scheduler build queue behavior."""

from __future__ import annotations

import pytest

from pynixd.build_queue import BuildQueue
from pynixd.serde import BasicDerivation, BuildDerivationRequest, BuildMode, StorePath as SerdeStorePath
from pynixd.store_path import StorePath


def _build_request(drv_path: StorePath, *, builder: str = "") -> BuildDerivationRequest:
    return BuildDerivationRequest(
        drv_path=SerdeStorePath(path=str(drv_path)),
        derivation=BasicDerivation(platform="x86_64-linux", builder=builder),
        build_mode=BuildMode.NORMAL,
    )


@pytest.mark.anyio
async def test_build_queue_deduplicates_active_builds_by_drv_path() -> None:
    queue = BuildQueue()
    drv_path = StorePath("/nix/store/00000000000000000000000000000001-test.drv")

    first_id, first_future = await queue.enqueue(_build_request(drv_path, builder="/bin/first"))
    second_id, second_future = await queue.enqueue(_build_request(drv_path, builder="/bin/second"))

    assert second_id == first_id
    assert second_future is first_future
    assert len(queue.queue) == 1
    assert queue.by_path[str(drv_path)].build_id == first_id


@pytest.mark.anyio
async def test_a_build_that_no_goal_waits_for_ends() -> None:
    """The last goal system that lets go ends the build.

    `build.sh:269` of Nix's functional suite reads this. It builds the
    `cancelled-builds` fixture with `-j2`. The `slow` derivation of that
    fixture writes to a fifo, which blocks until a reader opens the fifo, and
    the only reader is `fast-fail`, which fails. The test then removes the
    directory of the fifo, so no reader can ever appear. Nix answers in about
    two seconds and kills that builder, and pynixd left it running until the
    300 s timeout of the test. Issue #196.
    """
    queue = BuildQueue()
    drv_path = StorePath("/nix/store/00000000000000000000000000000001-slow.drv")

    build_id, future = await queue.enqueue(_build_request(drv_path), from_goal_path=True)
    assert queue.by_id[build_id].goal_holders == 1

    await queue.let_go(build_id)

    assert future.done()
    assert "no goal waits for it" in str(future.result().result.error_msg)


@pytest.mark.anyio
async def test_a_build_that_another_request_still_wants_runs_on() -> None:
    """A build of pynixd serves every client that asked for the same derivation.

    `_the_result_unless_it_stops` in `goals/requests.py` states the rule, and
    this is the count that makes it hold: one request that gives up takes no
    work from the other. Issue #196.
    """
    queue = BuildQueue()
    drv_path = StorePath("/nix/store/00000000000000000000000000000001-slow.drv")

    first_id, future = await queue.enqueue(_build_request(drv_path), from_goal_path=True)
    second_id, _ = await queue.enqueue(_build_request(drv_path), from_goal_path=True)

    assert second_id == first_id
    assert queue.by_id[first_id].goal_holders == 2

    await queue.let_go(first_id)
    assert not future.done()

    await queue.let_go(first_id)
    assert future.done()


@pytest.mark.anyio
async def test_a_build_that_no_goal_ever_held_is_left_alone() -> None:
    """`from_goal_path` marks the calls that take a reference, and no other."""
    queue = BuildQueue()
    drv_path = StorePath("/nix/store/00000000000000000000000000000001-slow.drv")

    build_id, future = await queue.enqueue(_build_request(drv_path))

    assert queue.by_id[build_id].goal_holders == 0

    await queue.let_go(build_id)

    assert not future.done()
