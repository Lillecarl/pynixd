"""Builds and everything else take connections from separate pools. Issue #97.

A build holds its connection for the whole build. On an SSH builder with 8
slots, 8 running builds held every read to that builder until `read_timeout`
cut it, with an error that blamed a daemon nobody had asked.

Each held connection lives in its own task: the pool reads a second acquire
from one task as re-entrant and gives it a connection past every limit.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import anyio

from nix_daemon_protocol.ids import StoreId
from pynixd.config import PynixdSettings
from pynixd.context import PynixdContext
from pynixd.state import collect
from tests.functional.mock_store import MockStore

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractAsyncContextManager

    from anyio.abc import TaskGroup

    from pynixd.connection import Connection


async def _hold(
    tg: TaskGroup, acquire: Callable[[], AbstractAsyncContextManager[Connection]], count: int, release: anyio.Event
) -> None:
    """Take *count* connections, each in a task of its own, and keep them until *release*."""
    taken = anyio.Semaphore(0)

    async def one() -> None:
        async with acquire():
            taken.release()
            await release.wait()

    for _ in range(count):
        tg.start_soon(one)
    for _ in range(count):
        with anyio.fail_after(1):
            await taken.acquire()


async def _got_one_within(acquire: Callable[[], AbstractAsyncContextManager[Connection]], seconds: float) -> bool:
    with anyio.move_on_after(seconds):
        async with acquire():
            return True
    return False


async def test_full_build_slots_leave_reads_a_connection() -> None:
    store = MockStore("lanes", feature_matrix={"x86_64-linux": set()})
    release = anyio.Event()
    async with anyio.create_task_group() as tg:
        await _hold(tg, store.build_conn, store.build_pool.max_connections, release)

        got_a_read = await _got_one_within(store.transfer_conn, 1)
        got_a_build = await _got_one_within(store.build_conn, 0.2)
        release.set()

        assert got_a_read
        assert not got_a_build, "a build got a slot past the build pool's size"


async def test_a_build_connection_closes_when_returned_and_a_transfer_one_waits() -> None:
    """A daemon worker keeps a temporary root on each output it built or
    checked, for as long as it lives. An idle build connection kept those
    roots past the build, and `multiple-outputs.sh:80` of Nix's suite could
    not delete the output around pynixd."""
    store = MockStore("lanes-reuse", feature_matrix={"x86_64-linux": set()})

    async with store.build_conn():
        pass
    async with store.transfer_conn():
        pass

    assert store.build_pool.all_conns == []
    assert store.build_pool.idle_conns == []
    assert len(store.transfer_pool.idle_conns) == 1


async def test_state_shows_each_pool_and_who_waits() -> None:
    store = MockStore("lanes-state", feature_matrix={"x86_64-linux": set()})
    ctx = PynixdContext(settings=PynixdSettings(), _stores={StoreId("lanes-state"): store})
    release = anyio.Event()
    async with anyio.create_task_group() as tg:
        await _hold(tg, store.build_conn, store.build_pool.max_connections, release)

        async def one_more_build() -> None:
            async with store.build_conn():
                pass

        tg.start_soon(one_more_build)
        await anyio.sleep(0.1)

        stores = collect(ctx, ["stores"]).stores
        release.set()

        assert stores is not None
        pools = stores["lanes-state"].pools
        assert pools["build"].in_use == pools["build"].slots
        assert pools["build"].waiting == 1
        assert pools["transfer"].waiting == 0
