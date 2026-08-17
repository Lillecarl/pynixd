"""Scheduler-owned substitution queue and availability cache."""

from __future__ import annotations

import asyncio
import contextlib
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

import anyio
import structlog
from cachetools import TTLCache

from .exceptions import OpNotImplementedError
from .serde import AddToStoreNarRequest, NarFromPathRequest, QueryPathInfoRequest, StorePath as SerdeStorePath
from .serde.context import ReadContext, WriteContext
from .serde.ids import LOCAL_STORE_ID
from .serde.valid_path_info import ValidPathInfo
from .store import DaemonStore, HTTPBinaryCacheStore

if TYPE_CHECKING:
    from collections.abc import Iterable

    from .config import PynixdSettings
    from .context import PynixdContext
    from .serde.ids import StoreId
    from .store import Store
    from .store.http_binary_cache import HTTPNarInfo
    from .store_path import StorePath

log = structlog.get_logger(__name__)
_NAR_CHUNK_SIZE = 1024 * 256


@dataclass(frozen=True)
class SubstitutionQueryResult:
    """Cached path-info query result for one path on one substituter."""

    store_id: StoreId
    path_info: ValidPathInfo | None
    query_succeeded: bool

    @property
    def found(self) -> bool:
        return self.path_info is not None


@dataclass(frozen=True)
class SubstitutionAvailability:
    """Fast availability answer for planning and graph traversal."""

    available: bool
    nar_size: int | None = None
    download_size: int | None = None

    @classmethod
    def unavailable(cls) -> SubstitutionAvailability:
        return cls(available=False)

    @classmethod
    def from_query_result(cls, result: SubstitutionQueryResult) -> SubstitutionAvailability:
        if result.path_info is None:
            return cls.unavailable()
        nar_size = result.path_info.info.nar_size
        return cls(available=True, nar_size=nar_size, download_size=nar_size)


@dataclass(frozen=True)
class SubstitutionCandidate:
    """Selected substituter source for one path."""

    store: Store
    path_info: ValidPathInfo
    http_narinfo: HTTPNarInfo | None = None


@dataclass(frozen=True)
class SubstitutionImportResult:
    """Result of importing one path into the local store."""

    substituted: bool
    path: StorePath
    candidate: SubstitutionCandidate | None = None
    error: str = ""


class SubstitutionHealthLog:
    """Fixed-size per-store substitution query health log."""

    def __init__(self, settings: PynixdSettings) -> None:
        self._entries: deque[bool] = deque(maxlen=settings.substitution_health_window)
        self._min_entries = max(
            1,
            int(settings.substitution_health_window * settings.substitution_health_min_fill_ratio),
        )
        self._min_success_ratio = settings.substitution_health_min_success_ratio

    def record(self, *, query_succeeded: bool) -> None:
        self._entries.append(query_succeeded)

    @property
    def is_healthy(self) -> bool:
        if len(self._entries) < self._min_entries:
            return True
        successes = sum(1 for entry in self._entries if entry)
        return successes / len(self._entries) > self._min_success_ratio


class SubstitutionQueue:
    """Owns substitution availability state for the scheduler singleton."""

    def __init__(self, ctx: PynixdContext) -> None:
        self.ctx = ctx
        settings = ctx.settings
        self.positive = cast(
            "TTLCache[StorePath, dict[StoreId, SubstitutionQueryResult], float]",
            TTLCache(maxsize=settings.substitution_cache_maxsize, ttl=settings.substitution_positive_ttl),
        )
        self.negative = cast(
            "TTLCache[StorePath, dict[StoreId, SubstitutionQueryResult], float]",
            TTLCache(maxsize=settings.substitution_cache_maxsize, ttl=settings.substitution_negative_ttl),
        )
        self.health: dict[StoreId, SubstitutionHealthLog] = {}
        self._probe_tasks: set[asyncio.Task[None]] = set()
        self._active_imports: dict[StorePath, asyncio.Task[SubstitutionImportResult]] = {}

    async def close(self) -> None:
        for task in self._probe_tasks:
            task.cancel()
        for task in list(self._probe_tasks):
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await task

    async def can_substitute(self, path: StorePath) -> SubstitutionAvailability:
        cached = self._cached_positive(path)
        if cached is not None:
            return SubstitutionAvailability.from_query_result(cached)

        stores = list(self.substituter_stores())
        if not stores:
            return SubstitutionAvailability.unavailable()

        to_probe = [store for store in stores if not self._has_cached_result(path, store.store_id)]
        if not to_probe:
            return SubstitutionAvailability.unavailable()

        done = anyio.Event()
        lock = anyio.Lock()
        first_positive: SubstitutionQueryResult | None = None
        healthy_pending = {store.store_id for store in to_probe if self.should_wait_for(store.store_id)}

        async def probe_store(store: Store) -> None:
            nonlocal first_positive
            result = await self._query_store(path, store)
            self.record_query_result(path, result)
            async with lock:
                if result.found and first_positive is None:
                    first_positive = result
                    done.set()
                healthy_pending.discard(result.store_id)
                if not healthy_pending:
                    done.set()

        for store in to_probe:
            self._track_probe_task(asyncio.create_task(probe_store(store)))

        if not healthy_pending:
            return SubstitutionAvailability.unavailable()

        await done.wait()
        if first_positive is not None:
            return SubstitutionAvailability.from_query_result(first_positive)
        return SubstitutionAvailability.unavailable()

    async def get_substituter(self, path: StorePath) -> SubstitutionCandidate | None:
        stores = list(self.substituter_stores())
        if not stores:
            return None

        stale_stores = [store for store in stores if not self._has_cached_result(path, store.store_id)]
        if stale_stores:
            await self._query_stores_for_selection(path, stale_stores)

        return self._best_candidate_from_cache(path, stores)

    async def substitute(self, path: StorePath) -> SubstitutionImportResult:
        existing = self._active_imports.get(path)
        if existing is not None:
            return await existing

        task = asyncio.create_task(self._substitute_uncached(path))
        self._active_imports[path] = task
        try:
            return await task
        finally:
            self._active_imports.pop(path, None)

    def record_query_result(self, path: StorePath, result: SubstitutionQueryResult) -> None:
        cache = self.positive if result.found else self.negative
        per_store = cache.setdefault(path, {})
        per_store[result.store_id] = result
        self.health_for(result.store_id).record(query_succeeded=result.query_succeeded)

    def health_for(self, store_id: StoreId) -> SubstitutionHealthLog:
        log = self.health.get(store_id)
        if log is None:
            log = SubstitutionHealthLog(self.ctx.settings)
            self.health[store_id] = log
        return log

    def should_wait_for(self, store_id: StoreId) -> bool:
        return self.health_for(store_id).is_healthy

    def substituter_stores(self) -> Iterable[Store]:
        return (
            store
            for store_id, store in sorted(self.ctx.stores.items(), key=lambda item: str(item[0]))
            if store_id != LOCAL_STORE_ID and store.no_schedule
        )

    async def _query_store(self, path: StorePath, store: Store) -> SubstitutionQueryResult:
        try:
            with anyio.fail_after(self.ctx.settings.substitution_query_timeout):
                response = await store.execute(QueryPathInfoRequest(path=SerdeStorePath(path=str(path))))
        except OpNotImplementedError:
            return SubstitutionQueryResult(store_id=store.store_id, path_info=None, query_succeeded=False)
        except TimeoutError:
            log.warning("substitution_query_timeout", store_id=store.store_id, path=str(path))
            return SubstitutionQueryResult(store_id=store.store_id, path_info=None, query_succeeded=False)
        except Exception:
            log.warning("substitution_query_failed", store_id=store.store_id, path=str(path), exc_info=True)
            return SubstitutionQueryResult(store_id=store.store_id, path_info=None, query_succeeded=False)

        if not response.valid or response.info is None:
            return SubstitutionQueryResult(store_id=store.store_id, path_info=None, query_succeeded=True)

        path_info = ValidPathInfo(path=SerdeStorePath(path=str(path)), info=response.info)
        return SubstitutionQueryResult(store_id=store.store_id, path_info=path_info, query_succeeded=True)

    async def _query_stores_for_selection(self, path: StorePath, stores: list[Store]) -> None:
        healthy_stores = [store for store in stores if self.should_wait_for(store.store_id)]
        background_stores = [store for store in stores if store not in healthy_stores]

        async def query_and_record(store: Store) -> None:
            self.record_query_result(path, await self._query_store(path, store))

        async with anyio.create_task_group() as tg:
            for store in healthy_stores:
                tg.start_soon(query_and_record, store)

        for store in background_stores:
            self._track_probe_task(asyncio.create_task(query_and_record(store)))

    async def _substitute_uncached(self, path: StorePath) -> SubstitutionImportResult:
        candidate = await self.get_substituter(path)
        if candidate is None:
            return SubstitutionImportResult(substituted=False, path=path, error=f"no substituter has path: {path}")

        try:
            await self._import_nar(path, candidate)
        except Exception as exc:
            log.warning("substitution_import_failed", store_id=candidate.store.store_id, path=str(path), exc_info=True)
            return SubstitutionImportResult(substituted=False, path=path, candidate=candidate, error=str(exc))
        return SubstitutionImportResult(substituted=True, path=path, candidate=candidate)

    async def _import_nar(self, path: StorePath, candidate: SubstitutionCandidate) -> None:
        async with self.ctx.local_store.transfer_conn() as conn:
            request = AddToStoreNarRequest(
                info=candidate.path_info,
                repair=0,
                dont_check_sigs=1,
            )
            await request.to_writer(WriteContext.from_conn(conn))
            await conn.w.drain()

            framed = conn.w.framed()
            if isinstance(candidate.store, HTTPBinaryCacheStore):
                http_narinfo = candidate.http_narinfo
                if http_narinfo is None:
                    http_narinfo = await candidate.store.get_narinfo(path)
                if http_narinfo is None:
                    raise RuntimeError(f"substituter lost path while streaming: {path}")
                async for chunk in candidate.store.stream_nar(http_narinfo):
                    framed.write(chunk)
                    await conn.w.drain()
            elif isinstance(candidate.store, DaemonStore):
                await self._stream_from_daemon(path, candidate, framed, conn)
            else:
                raise TypeError(f"store {candidate.store.store_id} cannot stream NARs")

            await framed.finalize()
            await request.response_type.from_reader(ReadContext.from_conn(conn))

    async def _stream_from_daemon(
        self, path: StorePath, candidate: SubstitutionCandidate, framed: Any, destination_conn: Any
    ) -> None:
        if not isinstance(candidate.store, DaemonStore):
            raise TypeError(f"store {candidate.store.store_id} cannot stream NARs")
        async with candidate.store.transfer_conn() as source_conn:
            await NarFromPathRequest(path=SerdeStorePath(path=str(path))).to_writer(WriteContext.from_conn(source_conn))
            await source_conn.w.drain()
            await source_conn.r.drain_stderr()

            remaining = candidate.path_info.info.nar_size
            while remaining > 0:
                chunk = await source_conn.r.readexactly(min(remaining, _NAR_CHUNK_SIZE))
                framed.write(chunk)
                await destination_conn.w.drain()
                remaining -= len(chunk)

    def _best_candidate_from_cache(self, path: StorePath, stores: list[Store]) -> SubstitutionCandidate | None:
        positive = self.positive.get(path, {})
        candidates = [
            SubstitutionCandidate(store=store, path_info=result.path_info)
            for store in stores
            if (result := positive.get(store.store_id)) is not None and result.path_info is not None
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda candidate: candidate.store.priority)

    def _cached_positive(self, path: StorePath) -> SubstitutionQueryResult | None:
        for result in self.positive.get(path, {}).values():
            if result.found:
                return result
        return None

    def _has_cached_result(self, path: StorePath, store_id: StoreId) -> bool:
        return store_id in self.positive.get(path, {}) or store_id in self.negative.get(path, {})

    def _track_probe_task(self, task: asyncio.Task[None]) -> None:
        self._probe_tasks.add(task)

        def done_callback(done_task: asyncio.Task[None]) -> None:
            self._probe_tasks.discard(done_task)
            if done_task.cancelled():
                return
            try:
                done_task.result()
            except Exception:
                log.warning("substitution_probe_task_failed", exc_info=True)

        task.add_done_callback(done_callback)
