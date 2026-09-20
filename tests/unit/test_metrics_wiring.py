"""The new series are wired to the code that moves them.

A metric that is declared and never incremented exports a flat zero, and a
flat zero reads on a dashboard exactly like a quiet system. These assert the
increment, not the declaration.

Every assertion is a **delta**. The registry is global and counters survive
across tests in one process, so an absolute value here would depend on what
ran first.
"""

from __future__ import annotations

import gc
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest
from prometheus_client import REGISTRY

from pynixd import metrics
from pynixd.proxy import DaemonProxy

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pynixd.store.pool import ConnectionPool


def _value(name: str, labels: dict[str, str]) -> float:
    """A sample, with an unused series read as zero.

    `get_sample_value` answers `None` for a labelled series nothing has
    touched yet, and `None` cannot be subtracted.
    """
    got = REGISTRY.get_sample_value(name, labels)
    return 0.0 if got is None else got


class FakeReader:
    def __init__(self, ops: Sequence[int]) -> None:
        self.remaining = list(ops)

    async def read_uint64(self) -> int:
        if not self.remaining:
            raise EOFError
        return self.remaining.pop(0)


class FakeProxy:
    """Enough of `DaemonProxy` for `op_loop` to run, as in
    `test_unknown_operation_ends_the_connection.py`."""

    def __init__(self, ops: Sequence[int], *, fail: bool = False) -> None:
        self.r = FakeReader(ops)
        self.w = SimpleNamespace(drain=self._nothing)
        self.client = SimpleNamespace(flush=self._nothing)
        self.errors: list[str] = []
        self._op_timing: dict[int, tuple[int, float]] = {}
        self._fail = fail

    async def _nothing(self) -> None:
        return None

    async def send_error(self, text: str) -> None:
        self.errors.append(text)

    async def dispatch(self, op_num: int) -> None:
        if self._fail:
            raise RuntimeError("the store refused")
        return None

    async def run(self) -> None:
        await DaemonProxy.op_loop(cast("DaemonProxy", self))


_IS_VALID_PATH = 1


class TestDaemonOperations:
    """`op_loop` timed every operation into a dict it printed at session end.

    That log line answers nothing while a transfer runs, which is when
    somebody looks at it.
    """

    @pytest.mark.anyio
    async def test_a_served_operation_counts_as_ok(self) -> None:
        labels = {"op": "IsValidPath", "result": "ok"}
        before = _value("pynixd_daemon_ops_total", labels)

        await FakeProxy([_IS_VALID_PATH, _IS_VALID_PATH]).run()

        assert _value("pynixd_daemon_ops_total", labels) - before == 2

    @pytest.mark.anyio
    async def test_a_failed_operation_counts_as_error(self) -> None:
        """The negative control. A counter with `result` hard-coded to `ok`
        would pass the test above."""
        labels = {"op": "IsValidPath", "result": "error"}
        before = _value("pynixd_daemon_ops_total", labels)

        proxy = FakeProxy([_IS_VALID_PATH], fail=True)
        await proxy.run()

        assert proxy.errors
        assert _value("pynixd_daemon_ops_total", labels) - before == 1

    @pytest.mark.anyio
    async def test_the_duration_is_observed_under_the_operation_name(self) -> None:
        labels = {"op": "IsValidPath"}
        before = _value("pynixd_daemon_op_duration_seconds_count", labels)

        await FakeProxy([_IS_VALID_PATH]).run()

        assert _value("pynixd_daemon_op_duration_seconds_count", labels) - before == 1

    def test_the_label_comes_from_the_wire_registry(self) -> None:
        """Cardinality. `op` must be a name of the protocol and never a number
        a client chose, or one bad client writes a series per request."""
        names = {
            sample.labels["op"]
            for metric in REGISTRY.collect()
            if metric.name == "pynixd_daemon_ops"
            for sample in metric.samples
        }
        assert names
        assert all(not name.startswith("op_") for name in names)


class FakePool:
    """The three attributes the collector reads.

    A class and not a `SimpleNamespace`: the collector holds pools weakly, and
    `SimpleNamespace` cannot be weakly referenced.
    """

    def __init__(self, store_id: str, in_flight: int, idle: int, total: int) -> None:
        self.store_id = store_id
        self.active_connections = in_flight
        self.idle_conns = [object()] * idle
        self.all_conns = [object()] * total


class TestThePoolCollector:
    def _series(self, store_id: str) -> dict[str, float]:
        return {
            name: _value(name, {"store_id": store_id})
            for name in (
                "pynixd_store_pool_in_flight_connections",
                "pynixd_store_pool_idle_connections",
                "pynixd_store_pool_connections",
            )
        }

    def test_a_registered_pool_reports_its_three_counts(self) -> None:
        pool = FakePool("test-pool-counts", in_flight=2, idle=3, total=5)
        metrics.STORE_POOLS.register(cast("ConnectionPool", pool))

        series = self._series("test-pool-counts")

        assert series["pynixd_store_pool_in_flight_connections"] == 2
        assert series["pynixd_store_pool_idle_connections"] == 3
        assert series["pynixd_store_pool_connections"] == 5

    def test_a_pool_that_goes_away_stops_reporting(self) -> None:
        """The collector holds pools weakly. A store that is removed must not
        be kept alive by the registry, and must not leave a stale series that
        an alert reads as a pool nobody is draining."""
        pool = FakePool("test-pool-dropped", in_flight=1, idle=0, total=1)
        metrics.STORE_POOLS.register(cast("ConnectionPool", pool))
        assert self._series("test-pool-dropped")["pynixd_store_pool_connections"] == 1

        del pool
        gc.collect()

        assert self._series("test-pool-dropped")["pynixd_store_pool_connections"] == 0


class TestNarForwarding:
    def test_the_forward_series_exist_unlabelled(self) -> None:
        """`AddMultipleToStore` is the path a `nix copy` into pynixd takes, and
        it carried no series at all while it peaked at 0.979 of the payload in
        memory. These have no labels, so they are exported from the start and
        a dashboard has a line before the first transfer."""
        for name in (
            "pynixd_nar_forward_bytes_total",
            "pynixd_nar_forward_paths_total",
            "pynixd_nar_forward_source_eof_timeouts_total",
        ):
            assert REGISTRY.get_sample_value(name) is not None, name
