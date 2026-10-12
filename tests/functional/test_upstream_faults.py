"""pynixd against a daemon link that breaks. Issue #93.

A `FaultProxy` sits between pynixd's local store and a real nix-daemon and
breaks the link on request. For each fault the client must get an answer --
an error is an answer -- within a bound, and the next operation must work:
a broken connection leaves the pool, and pynixd keeps serving.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

import anyio
import pytest

from nix_daemon_protocol.ids import StoreId
from pynixd import Server
from pynixd.connection import Connection
from pynixd.serde import IsValidPathRequest, StorePath
from pynixd.store import LocalSocketStore
from pynixd.wire import UnixNixReader, UnixNixWriter
from tests._conftest.fault_proxy import Fault, FaultProxy
from tests.conftest import STORE_PREFIX, make_test_spec, rmtree_robust

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

_ABSENT = "/nix/store/00000000000000000000000000000000-absent"
_ANSWER_WITHIN = 10
"""Seconds a client may wait for any answer, error or not."""


@dataclass
class _Rig:
    server: Server
    proxy: FaultProxy
    client_socket: Path

    async def ask(self) -> bool:
        """One IsValidPath through pynixd, which forwards it to the daemon."""
        r, w = await asyncio.open_unix_connection(str(self.client_socket))
        conn = Connection(UnixNixReader(r, identifier="t"), UnixNixWriter(w, identifier="t"), "t")
        try:
            await conn.connect()
            response = await conn.call(IsValidPathRequest(path=StorePath(path=_ABSENT)), raise_on_error=True)
            return bool(response.valid)
        finally:
            await conn.close()

    async def ask_within_bound(self) -> BaseException | bool:
        with anyio.fail_after(_ANSWER_WITHIN):
            try:
                return await self.ask()
            except Exception as ex:  # noqa: BLE001 -- an error is an answer here
                return ex


@pytest.fixture
async def rig() -> AsyncIterator[_Rig]:
    root = STORE_PREFIX / "upstream-faults"
    rmtree_robust(root)
    root.mkdir(parents=True)
    daemon = LocalSocketStore(make_test_spec(store_id="faults-daemon", store_path=root / "store", no_probe=True))
    await daemon.ensure_daemon()
    proxy = FaultProxy(root / "proxy.sock", daemon.socket_path)
    await proxy.start()
    local = LocalSocketStore(
        make_test_spec(
            store_id="local",
            store_path=root / "store",
            no_probe=True,
            managed=False,
            socket_path=root / "proxy.sock",
        )
    )
    client_socket = root / "pynixd.sock"
    try:
        async with Server(
            stores={StoreId("local"): local},
            unix_path=client_socket,
            ssh_port=None,
            http_port=None,
        ) as server:
            rig = _Rig(server, proxy, client_socket)
            # Warm the pool, so an armed fault lands mid-operation and not
            # in a handshake.
            assert await rig.ask() is False
            yield rig
    finally:
        await proxy.close()
        await daemon.close()


_UNBOUNDED = "pynixd has no bound on an upstream that stops answering (#93)"


@pytest.mark.parametrize(
    "fault",
    [
        Fault.CLOSE,
        Fault.RESET,
        # Flipped bytes read as a huge length, and pynixd waits for bytes the
        # daemon never sends: a stall by another road.
        pytest.param(Fault.CORRUPT, marks=pytest.mark.xfail(strict=True, reason=_UNBOUNDED)),
    ],
)
async def test_a_broken_response_fails_the_operation_and_the_next_one_works(rig: _Rig, fault: Fault) -> None:
    rig.proxy.arm(fault)

    answer = await rig.ask_within_bound()

    assert rig.proxy.triggered.is_set(), "the operation never reached the daemon"
    assert isinstance(answer, BaseException), f"{fault.value}: the client got {answer!r} from a broken link"
    assert await rig.ask_within_bound() is False


async def test_links_that_die_while_idle_cost_the_next_client_nothing(rig: _Rig) -> None:
    """A daemon restart closes every pooled link. The pool reconnects."""
    rig.proxy.cut_every_link()
    await anyio.sleep(0.1)

    assert await rig.ask_within_bound() is False


async def test_a_daemon_that_is_gone_fails_fast_and_pynixd_recovers(rig: _Rig) -> None:
    rig.proxy.cut_every_link()
    await rig.proxy.stop_listening()

    answer = await rig.ask_within_bound()
    assert isinstance(answer, BaseException)

    await rig.proxy.start()
    assert await rig.ask_within_bound() is False


@pytest.mark.xfail(strict=True, reason=_UNBOUNDED)
async def test_a_daemon_that_stops_answering_still_gets_the_client_an_answer(rig: _Rig) -> None:
    rig.proxy.arm(Fault.STALL)

    answer = await rig.ask_within_bound()

    assert rig.proxy.triggered.is_set()
    assert isinstance(answer, BaseException)
