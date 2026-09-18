"""The collector's arithmetic, without a store.

`tests/functional/test_gc_substituter.py` proves the same rules against a real
store and a real cache. This file proves the two cases that a real store cannot
reach: a local store that cannot answer `QueryClosure`, and a substituter that
raises.
"""

from __future__ import annotations

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
class FakeLocal:
    """The local store: it answers from `REFERENCES` and records the deletes."""

    answers_closure: bool = True
    gc_defer: bool = False
    live: set[str] = field(default_factory=set)
    deleted: list[str] = field(default_factory=list)

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
) -> tuple[Collector, FakeLocal]:
    local = FakeLocal(answers_closure=answers_closure, live=live or set())
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
