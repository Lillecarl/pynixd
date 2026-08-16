"""An SSH store holds its connection, unless it is told not to.

Holding it is the default and is the point: the connection's state is the
health of the store. The reconnect loop, the backoff and the circuit breaker
all read it, and the monitor polls over it, so a builder that is up looks up
because the socket is there. That measurement is worth keeping.

`persistent_connection = false` gives it up for a builder that starts on
demand. Measured on a laptop whose `vz-builder` VM is started by a
socket-activated unit and stopped by a watchdog about 90 seconds after the
last connection closes: 25 minutes idle, no build ever submitted,
`vfkit --cpus 15 --memory 8192` still resident, and the one ESTABLISHED
socket was pynixd's. `probe = false` and `monitor = false` did not prevent
it, because both are read after the connect.

Deferring the connect is only half of that mode. The `asyncssh` connection
outlives every channel opened on it, so the first build would pin the builder
just as hard and half as visibly. `ConnectionPool` reports when it holds
nothing at all, and the store drops the transport there.

See issue #164.
"""

from __future__ import annotations

import anyio
import pytest

from pynixd.config import SSHSubprocessStoreSpec
from pynixd.monitor import ResourceGate
from pynixd.serde.ids import StoreId
from pynixd.store.pool import ConnectionPool
from pynixd.store.ssh import SSHStore


class RecordingSSHStore(SSHStore):
    """An `SSHStore` that records the connect calls instead of making them."""

    def __init__(self, spec: SSHSubprocessStoreSpec) -> None:
        super().__init__(spec)
        self.host = spec.host
        self.port = spec.port
        self.username = spec.username
        self.init_ssh_state(
            monitor_enabled=spec.monitor,
            persistent_connection=spec.persistent_connection,
        )
        self.connects = 0
        self.disconnects = 0

    async def ensure_ssh(self):  # type: ignore[override] -- the test replaces the transport
        self.connects += 1
        self.conn = object()
        return self.conn

    async def close_ssh(self) -> None:
        self.disconnects += 1
        self.conn = None

    async def create_conn(self):  # type: ignore[override] -- nothing to dial here
        raise AssertionError("this test opens no channel")


def _store(*, persistent: bool = True) -> RecordingSSHStore:
    return RecordingSSHStore(
        SSHSubprocessStoreSpec(
            store_id=StoreId("builder"),
            host="vz-builder",
            port=31122,
            monitor=False,
            persistent_connection=persistent,
        )
    )


@pytest.mark.anyio
async def test_the_default_store_connects_at_startup() -> None:
    """The default, and the reason it is the default.

    A remote builder's health is read from this connection. Deferring it for
    every store would trade a working measurement for an edge case.

    `DaemonStore.start` is not reached -- it needs a live daemon -- so the
    claim under test is narrowly that `SSHStore.start` dials first.
    """
    store = _store()
    with pytest.raises(Exception):  # noqa: B017 -- the parent needs a live daemon
        await store.start(sync_paths=False)
    assert store.connects == 1


@pytest.mark.anyio
async def test_an_on_demand_store_does_not_connect_before_it_is_used() -> None:
    """The edge case, stated at the method that carried the defect."""
    store = _store(persistent=False)
    assert store.connects == 0

    with pytest.raises(Exception):  # noqa: B017 -- the parent needs a live daemon
        await store.start(sync_paths=False)

    assert store.connects == 0, (
        "SSHStore.start opened a connection with persistent_connection off. "
        "A builder that starts on demand never stops again."
    )


@pytest.mark.anyio
async def test_an_on_demand_transport_is_dropped_once_the_pool_is_empty() -> None:
    """The other half. Without it the first build pins the builder for good."""
    store = _store(persistent=False)
    await store.ensure_ssh()
    assert store.conn is not None

    await store._on_pool_empty()

    assert store.disconnects == 1
    assert store.conn is None


@pytest.mark.anyio
async def test_a_persistent_transport_survives_an_empty_pool() -> None:
    """The default keeps its connection, which is what health is read from."""
    store = _store()
    await store.ensure_ssh()

    await store._on_pool_empty()

    assert store.disconnects == 0
    assert store.conn is not None


@pytest.mark.anyio
async def test_dropping_a_transport_that_is_already_gone_is_quiet() -> None:
    """The sweep can reach an empty pool more than once."""
    store = _store(persistent=False)
    await store._on_pool_empty()
    assert store.disconnects == 0


class TestPoolEmptyNotification:
    """`ConnectionPool` tells its owner when the last connection goes."""

    def _pool(self, calls: list[int]) -> ConnectionPool:
        async def on_empty() -> None:
            calls.append(1)

        async def factory():
            raise AssertionError("this test creates no connection")

        return ConnectionPool(
            store_id="builder",
            factory=factory,
            gate=ResourceGate(),
            idle_ttl=0.01,
            on_pool_empty=on_empty,
        )

    @pytest.mark.anyio
    async def test_an_empty_pool_notifies(self) -> None:
        calls: list[int] = []
        await self._pool(calls)._notify_if_empty()
        assert calls == [1]

    @pytest.mark.anyio
    async def test_a_pool_holding_an_active_connection_does_not(self) -> None:
        calls: list[int] = []
        pool = self._pool(calls)
        pool.active_connections = 1
        await pool._notify_if_empty()
        assert calls == []

    @pytest.mark.anyio
    async def test_a_failing_callback_does_not_escape_the_sweep(self) -> None:
        """The sweep task has nobody to report to, so it must not die."""

        async def boom() -> None:
            raise RuntimeError("teardown failed")

        async def factory():
            raise AssertionError("this test creates no connection")

        pool = ConnectionPool(
            store_id="builder",
            factory=factory,
            gate=ResourceGate(),
            on_pool_empty=boom,
        )
        with anyio.fail_after(1):
            await pool._notify_if_empty()
