"""Daemon state collection, federation, and metrics readers. Issue #81."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import anyio
import pytest

from nix_daemon_protocol.ids import LOCAL_STORE_ID, StoreId
from pynixd import metrics, state as state_collector
from pynixd.daemon_extensions.pynixd_state import PynixdStateRequest
from pynixd.exceptions import OpNotImplementedError
from pynixd.handlers.pynixd_state import PynixdStateHandler, _federated_sections, _query_one
from pynixd.serde.auth import Role
from pynixd.serde.context import ReadContext, WriteContext
from pynixd.sessions import ClientSessions
from pynixd.wire import BytesReader, BytesWriter

if TYPE_CHECKING:
    from pynixd.context import PynixdContext

VERSION = 0x126


def test_metrics_readers_report_recorded_totals() -> None:
    before_bytes = metrics.nar_bytes_received_total()
    before_paths = metrics.nar_paths_received_total()
    metrics.NAR_FORWARD_BYTES.inc(100)
    metrics.NAR_ADD_BYTES.inc(50)
    metrics.NAR_FORWARD_PATHS.inc(3)
    assert metrics.nar_bytes_received_total() - before_bytes == 150
    assert metrics.nar_paths_received_total() - before_paths == 3

    before = metrics.builds_completed_by_status()
    metrics.BUILDS_COMPLETED.labels(status="success").inc(2)
    after = metrics.builds_completed_by_status()
    assert after["success"] - before["success"] == 2
    assert set(after) == {"success", "failure"}

    before_served = metrics.nar_bytes_served_total()
    before_accepted = metrics.sessions_accepted_by_transport()
    metrics.NAR_SERVE_BYTES.inc(7)
    metrics.DAEMON_SESSIONS_TOTAL.labels(transport="unix").inc()
    assert metrics.nar_bytes_served_total() - before_served == 7
    assert metrics.sessions_accepted_by_transport()["unix"] - before_accepted.get("unix", 0) == 1


def _build(done: bool = False, building: bool = False) -> SimpleNamespace:
    pending = not done and not building
    return SimpleNamespace(is_done=done, is_building=building, is_pending=pending)


def _ctx(
    builds: list[SimpleNamespace] | None = None,
    stores: dict[object, object] | None = None,
) -> SimpleNamespace:
    queue = SimpleNamespace(queue=list(builds or []))
    scheduler = SimpleNamespace(queue=queue)
    return SimpleNamespace(scheduler=scheduler, stores=stores or {}, sessions=ClientSessions())


def test_collect_counts_queue_by_state() -> None:
    ctx = _ctx(builds=[_build(done=True), _build(building=True), _build(), _build()])
    sections = state_collector.collect(cast("PynixdContext", ctx), ["queue"])
    assert sections == {"queue": {"scheduler": True, "pending": 2, "building": 1, "done": 1}}


def test_collect_without_scheduler_reports_none() -> None:
    ctx = SimpleNamespace(scheduler=None, stores={}, sessions=ClientSessions())
    sections = state_collector.collect(cast("PynixdContext", ctx), ["queue"])
    assert sections["queue"]["scheduler"] is False


def test_collect_names_stores_and_sessions() -> None:
    store = SimpleNamespace(is_healthy=True, feature_matrix={"x86_64-linux": {"kvm"}})
    ctx = _ctx(stores={StoreId("builder-a"): store})
    sections = state_collector.collect(cast("PynixdContext", ctx), ["stores", "sessions", "no-such-section"])
    assert sections["stores"] == {"builder-a": {"healthy": True, "systems": ["x86_64-linux"]}}
    assert sections["sessions"] == {}
    assert "no-such-section" not in sections


def test_collect_empty_wants_answers_everything() -> None:
    sections = state_collector.collect(cast("PynixdContext", _ctx()), [])
    assert set(sections) == set(state_collector.KNOWN_SECTIONS)


@pytest.mark.anyio
async def test_query_one_marks_a_hanging_store_unreachable() -> None:
    async def hang(request: Any) -> Any:
        await anyio.sleep(60.0)
        raise AssertionError("unreachable")

    store = SimpleNamespace(execute=hang)
    started = time.monotonic()
    answer = await _query_one(cast(Any, store), [], timeout=0.1)
    assert time.monotonic() - started < 10.0
    assert "no answer" in answer["error"]


@pytest.mark.anyio
async def test_query_one_marks_a_stock_daemon_unimplemented() -> None:
    async def refuse(request: Any) -> Any:
        raise OpNotImplementedError("nope")

    store = SimpleNamespace(execute=refuse)
    answer = await _query_one(cast(Any, store), [])
    assert answer == {"error": "state op not implemented by this store"}


class FakeProxy:
    def __init__(self, body: bytes, ctx: SimpleNamespace, stores: dict[object, object]) -> None:
        self.r = BytesReader(body, identifier="test:state")
        self.version = VERSION
        self.standard_features: frozenset[str] = frozenset()
        self.ctx = ctx
        self.stores = stores
        self.client: Any = None
        self.errors: list[str] = []

    async def send_error(self, message: str) -> None:
        self.errors.append(message)


@dataclass
class FakeContext:
    proxy: FakeProxy
    role: Role
    version: int = VERSION
    username: str = "test"


async def _body(wants: list[str], federated: bool) -> bytes:
    writer = BytesWriter("test")
    req = PynixdStateRequest(version=1, wants=wants, federated=federated)
    await req.to_writer(WriteContext(writer=writer, version=VERSION))
    return writer.get_bytes()[8:]


@pytest.mark.anyio
async def test_wire_round_trip_carries_wants_and_federated() -> None:
    body = await _body(["queue", "stores"], True)
    req = await PynixdStateRequest.from_reader(
        ReadContext(
            reader=BytesReader(body, identifier="test:state"),
            version=VERSION,
            features=frozenset(),
        )
    )
    assert req.wants == ["queue", "stores"]
    assert req.federated is True
    assert req.op == 111


@pytest.mark.anyio
async def test_handler_refuses_an_untrusted_client() -> None:
    proxy = FakeProxy(await _body([], False), _ctx(), {})
    resp = await PynixdStateHandler().handle(FakeContext(proxy=proxy, role=Role.USER))  # type: ignore[arg-type] -- fakes
    assert resp is None
    assert len(proxy.errors) == 1 and "administrative privileges" in proxy.errors[0]


@pytest.mark.anyio
async def test_handler_collects_local_sections() -> None:
    store = SimpleNamespace(is_healthy=False, feature_matrix={})
    ctx = _ctx(builds=[_build()], stores={StoreId("builder-a"): store})
    proxy = FakeProxy(await _body(["queue", "stores"], False), ctx, {})
    resp = await PynixdStateHandler().handle(FakeContext(proxy=proxy, role=Role.ADMIN))  # type: ignore[arg-type] -- fakes
    assert resp is not None
    payload = json.loads(cast(Any, resp).payload)
    assert payload["queue"]["pending"] == 1
    assert payload["stores"]["builder-a"] == {"healthy": False, "systems": []}
    assert "federated" not in payload


@pytest.mark.anyio
async def test_federated_merge_marks_failures() -> None:
    async def answer(request: Any) -> Any:
        return SimpleNamespace(payload=json.dumps({"queue": {"pending": 3}}))

    async def refuse(request: Any) -> Any:
        raise OpNotImplementedError("stock daemon")

    ctx = _ctx()
    proxy = FakeProxy(
        await _body([], False),
        ctx,
        {
            LOCAL_STORE_ID: SimpleNamespace(),
            StoreId("good"): SimpleNamespace(execute=answer),
            StoreId("old"): SimpleNamespace(execute=refuse),
        },
    )
    merged = await _federated_sections(cast(Any, FakeContext(proxy=proxy, role=Role.ADMIN)), ["queue"])
    assert merged["good"] == {"queue": {"pending": 3}}
    assert merged["old"] == {"error": "state op not implemented by this store"}
    assert LOCAL_STORE_ID not in merged and "local" not in merged
