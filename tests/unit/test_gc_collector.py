"""The collector's arithmetic, without a store.

`tests/functional/test_gc_substituter.py` proves the same rules against a real
store and a real cache. This file proves the two cases that a real store cannot
reach: a local store that cannot answer `QueryClosure`, and a substituter that
raises.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import pytest

from nix_daemon_protocol import (
    GCAction,
    QueryAllValidPathsRequest,
    QueryAllValidPathsResponse,
    QueryValidPathsRequest,
    QueryValidPathsResponse,
)
from nix_daemon_protocol.store_path import StorePath
from pynixd.daemon_extensions import (
    QueryClosureRequest,
    QueryClosureResponse,
    QueryPathInfosRequest,
    QueryPathInfosResponse,
)
from pynixd.exceptions import BackendError
from pynixd.gc import Collector

HELLO = "/nix/store/00000000000000000000000000000000-hello"
GLIBC = "/nix/store/11111111111111111111111111111111-glibc"
ONLY_HERE = "/nix/store/22222222222222222222222222222222-only-here"

REFERENCES = {HELLO: {GLIBC}, GLIBC: set(), ONLY_HERE: set()}


def _names(paths: Any) -> set[str]:
    """`StorePath` is not a `str`, and it prints the whole path."""
    return {str(path) for path in paths}


def _paths(names: Any) -> set[StorePath]:
    return {StorePath(str(name)) for name in names}


def _closure(seeds: Any) -> set[str]:
    out: set[str] = set()
    todo = list(_names(seeds))
    while todo:
        path = todo.pop()
        if path in out:
            continue
        out.add(path)
        todo.extend(REFERENCES.get(path, set()))
    return out


@dataclass
class FakeConn:
    """One held upstream connection: every delete of the queue rides it."""

    local: FakeLocal
    calls: int = 0

    async def call(self, request: Any, **_kwargs: Any) -> Any:
        paths = {str(path) for path in request.paths_to_delete}
        self.calls += 1
        self.local.sizes.append(len(paths))
        for path in paths:
            if path in self.local.refuse:
                raise BackendError(f"pynixd: fake refuses {path}")
            blocker = self.local.refuse_until_gone.get(path)
            if blocker is not None and blocker not in self.local.deleted:
                raise BackendError(f"pynixd: fake refuses {path} while {blocker} stands")
        kept = paths - self.local.silent
        self.local.deleted.extend(sorted(kept))
        return _Deleted({StorePath(path) for path in kept})


@dataclass
class FakeLocal:
    """The local store: it answers from `REFERENCES` and records the deletes."""

    answers_closure: bool = True
    gc_defer: bool = False
    live: set[str] = field(default_factory=set)
    deleted: list[str] = field(default_factory=list)
    refuse: set[str] = field(default_factory=set)
    refuse_until_gone: dict[str, str] = field(default_factory=dict)
    silent: set[str] = field(default_factory=set)
    sizes: list[int] = field(default_factory=list)
    acquired: int = 0

    async def execute(self, request: Any, **_kwargs: Any) -> Any:
        if isinstance(request, QueryAllValidPathsRequest):
            return QueryAllValidPathsResponse(paths=_paths(REFERENCES))
        if isinstance(request, QueryClosureRequest):
            # `DaemonStore.query_closure` answers an empty set for a store that
            # does not carry the feature.
            if not self.answers_closure:
                return QueryClosureResponse(paths=set())
            return QueryClosureResponse(paths=_paths(_closure(set(request.paths))))
        if isinstance(request, QueryPathInfosRequest):
            return QueryPathInfosResponse(infos=[])
        raise AssertionError(f"unexpected request {type(request).__name__}")

    async def retire_idle_connections(self) -> int:
        return 0

    async def probe(self) -> None:
        return None

    def transfer_conn(self, _options: Any = None) -> Any:
        return self._held()

    @asynccontextmanager
    async def _held(self) -> AsyncIterator[FakeConn]:
        self.acquired += 1
        yield FakeConn(self)

    async def call(self, request: Any, **_kwargs: Any) -> Any:
        if request.action == GCAction.RETURN_LIVE:
            # The wire coerces; this dataclass does not.
            return _Deleted(_paths(self.live))
        self.deleted.extend(str(path) for path in request.paths_to_delete)
        return _Deleted(set(request.paths_to_delete))


@dataclass
class _Deleted:
    paths_deleted: set[Any]
    bytes_freed: int = 1


@dataclass
class FakeCache:
    """A substituter. It holds `held`, or raises when `reachable` is false."""

    held: set[str]
    reachable: bool = True
    gc_defer: bool = True
    store_id: str = "upstream"

    async def execute(self, request: Any, **_kwargs: Any) -> Any:
        if not self.reachable:
            raise OSError("no route to host")
        if not isinstance(request, QueryValidPathsRequest):
            raise AssertionError(f"unexpected request {type(request).__name__}")
        return QueryValidPathsResponse(paths={path for path in request.paths if str(path) in self.held})


@dataclass
class FakeContext:
    local: FakeLocal
    cache: FakeCache | None

    @property
    def local_store(self) -> Any:
        return self.local

    @property
    def stores(self) -> dict[str, Any]:
        stores: dict[str, Any] = {"local": self.local}
        if self.cache is not None:
            stores["upstream"] = self.cache
        return stores


def _collector(
    cache: FakeCache | None,
    answers_closure: bool = True,
    live: set[str] | None = None,
    refuse: set[str] | None = None,
    refuse_until_gone: dict[str, str] | None = None,
    silent: set[str] | None = None,
) -> tuple[Collector, FakeLocal]:
    local = FakeLocal(
        answers_closure=answers_closure,
        live=live or set(),
        refuse=refuse or set(),
        refuse_until_gone=refuse_until_gone or {},
        silent=silent or set(),
    )
    return Collector(FakeContext(local=local, cache=cache)), local  # type: ignore[arg-type] -- a fake context


@pytest.mark.anyio
async def test_a_cached_path_is_droppable():
    collector, _local = _collector(FakeCache(held={HELLO, GLIBC}))

    assert _names(await collector.plan()) == {HELLO, GLIBC}


@pytest.mark.anyio
async def test_a_unique_referrer_keeps_what_it_references():
    """`ONLY_HERE` is nowhere upstream, and `HELLO` refers to `GLIBC`, so a
    cache that holds only `GLIBC` releases nothing: `HELLO` needs it."""
    collector, _local = _collector(FakeCache(held={GLIBC}))

    assert _names(await collector.plan()) == set()


@pytest.mark.anyio
async def test_a_live_path_stays_although_the_cache_has_it():
    """`gc.cc:778` throws on a live path and abandons the whole request, so
    the plan removes what Nix reported alive before it asks."""
    collector, _local = _collector(FakeCache(held={HELLO, GLIBC}), live={HELLO, GLIBC})

    assert _names(await collector.plan()) == set()


@pytest.mark.anyio
async def test_an_unreachable_substituter_holds_nothing():
    collector, _local = _collector(FakeCache(held={HELLO, GLIBC}, reachable=False))

    assert _names(await collector.plan()) == set()


@pytest.mark.anyio
async def test_a_store_without_the_closure_feature_drops_nothing():
    """The guard. Without it the empty answer would make every path droppable,
    including the ones no substituter has."""
    collector, _local = _collector(FakeCache(held={HELLO, GLIBC}), answers_closure=False)

    assert _names(await collector.plan()) == set()


@pytest.mark.anyio
async def test_no_substituter_named_drops_nothing():
    collector, local = _collector(None)

    assert _names(await collector.plan()) == set()
    assert local.deleted == []


@pytest.mark.anyio
async def test_delete_sends_each_path_alone_on_one_connection():
    """Issue #10: no single call carries the batch; one connection serves the queue."""
    collector, local = _collector(None)
    batch = [HELLO, GLIBC, ONLY_HERE]
    weights = {path: (10, 0) for path in batch}
    lines: list[Any] = []

    resp = await collector._delete(batch, weights, lines, None)

    assert local.deleted == batch
    assert local.acquired == 1
    assert local.sizes == [1, 1, 1]
    assert _names(resp.store_paths) == set(batch)
    assert lines[-1].text == f"deleted {len(batch)} paths, {len(batch)} bytes freed"


@pytest.mark.anyio
async def test_delete_refusal_mid_queue_deletes_the_rest():
    """A live path drops without stopping the queue; the next pass re-plans it."""
    collector, local = _collector(None, refuse={GLIBC})
    batch = [HELLO, GLIBC, ONLY_HERE]
    weights = {path: (10, 0) for path in batch}

    resp = await collector._delete(batch, weights, [], None)

    assert local.deleted == [HELLO, ONLY_HERE]
    assert local.sizes == [1, 1, 1, 1]
    assert _names(resp.store_paths) == {HELLO, ONLY_HERE}


@pytest.mark.anyio
async def test_delete_retries_a_path_its_referrer_blocked():
    """A child refused while its referrer stands goes on the retry lap.

    One request for the whole batch deleted referrer and reference
    together; one request per path meets the child first and the daemon
    refuses it as alive. The second lap finds the referrer gone.
    """
    collector, local = _collector(None, refuse_until_gone={GLIBC: HELLO})
    batch = [GLIBC, HELLO]
    weights = {path: (10, 0) for path in batch}

    resp = await collector._delete(batch, weights, [], None)

    assert sorted(local.deleted) == sorted([GLIBC, HELLO])
    assert _names(resp.store_paths) == {GLIBC, HELLO}


@pytest.mark.anyio
async def test_delete_silent_skip_stays_for_next_pass():
    """A path the daemon skips without error is spared, not counted deleted."""
    collector, local = _collector(None, silent={GLIBC})
    batch = [HELLO, GLIBC, ONLY_HERE]
    weights = {path: (10, 0) for path in batch}

    resp = await collector._delete(batch, weights, [], None)

    assert local.deleted == [HELLO, ONLY_HERE]
    assert _names(resp.store_paths) == {HELLO, ONLY_HERE}
