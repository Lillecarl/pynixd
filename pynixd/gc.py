"""The collector behind `PynixdCollectGarbage` (op 101).

One rule today: a path may leave the local store when a substituter that
`gc_defer` names already holds it, and when Nix agrees that it is not alive.

**The rule is not liveness alone.** The store pynixd serves is a cache, and
almost nothing in it is rooted, so `GCAction.DELETE_DEAD` would empty it. This
collector names every path that it deletes.

Age is the second rule, and it is here: `gc_max_age` on the local store
plans the dead paths nothing referenced for that many seconds, with no
substituter needed. Size is the third: every pass deletes in weight order,
`(nar_size, age)` blended by disk pressure, so an empty disk collects
oldest first and a full disk biggest first. `gc_target_usage` bounds a
pass by pressure instead of emptying the plan at once.
"""

from __future__ import annotations

import shutil
import time
from typing import TYPE_CHECKING

import structlog
from anyio.to_thread import run_sync

from nix_daemon_protocol import (
    CollectGarbageRequest,
    GCAction,
    LogNext,
    QueryAllValidPathsRequest,
    QueryValidPathsRequest,
    StorePath,
)

from .daemon_extensions import (
    PynixdCollectGarbageResponse,
    PynixdGCAction,
    QueryClosureRequest,
    QueryPathInfosRequest,
)
from .exceptions import BackendError, GCNotPermittedError
from .liveness import RootsTracker, walk_volatile
from .local_store_db import resolve_db_path
from .store import is_http_binary_cache

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from .connection import ClientConn
    from .context import PynixdContext
    from .store.base import Store

log = structlog.get_logger(__name__)

_MAX_FREED = 2**63 - 1


def _request(action: GCAction, paths: set[StorePath]) -> CollectGarbageRequest:
    return CollectGarbageRequest(
        action=action,
        paths_to_delete=paths,
        ignore_liveness=0,
        max_freed=_MAX_FREED,
        obsolete1=0,
        obsolete2=0,
        obsolete3=0,
    )


def _by_weight(weights: Mapping[str, tuple[int, int]], pressure: float) -> list[str]:
    """The paths in delete order: size matters more the fuller the disk is.

    Pure, so the order a pass deletes in is asserted without a daemon.
    `weights` maps the path string to `(nar_size, age_seconds)`; `pressure`
    is the disk usage fraction. Both axes normalise against the candidate
    set, and the score blends them by pressure:

        score = pressure * size_norm + (1 - pressure) * age_norm

    An empty disk collects oldest first, a full disk biggest first, and
    every pressure between moves continuously: no threshold flips the order
    between two passes. Descending score, path string breaking ties so the
    order is stable run to run.
    """
    sizes = {path: size for path, (size, _) in weights.items()}
    ages = {path: age for path, (_, age) in weights.items()}
    biggest = max(sizes.values(), default=0)
    oldest = max(ages.values(), default=0)
    scored = {
        path: (
            pressure * (size / biggest if biggest else 0.0)
            + (1.0 - pressure) * (ages[path] / oldest if oldest else 0.0)
        )
        for path, size in sizes.items()
    }
    return [path for path, _ in sorted(scored.items(), key=lambda item: (-item[1], item[0]))]


def _take_until_below_target(ordered: Sequence[tuple[str, int]], used: int, total: int, target: float) -> list[str]:
    """The leading paths whose removal projects usage under `target`.

    Pure, for the same reason. `ordered` is `(path, size)` heaviest first;
    the walk stops at the first prefix whose freed bytes bring `used / total`
    to or under the fraction. A target already met takes nothing; a target
    no prefix meets takes everything.
    """
    batch: list[str] = []
    freed = 0
    for path, size in ordered:
        if (used - freed) / total <= target:
            break
        batch.append(path)
        freed += size
    return batch


class Collector:
    """Chooses what leaves the local store, and asks Nix to delete it."""

    def __init__(self, ctx: PynixdContext) -> None:
        self.ctx = ctx

    async def run(
        self,
        action: PynixdGCAction,
        client: ClientConn | None = None,
        limit: int | None = None,
        target_usage: float | None = None,
    ) -> PynixdCollectGarbageResponse:
        """Plan a pass, and run it when *action* says to.

        A plan is not free. It asks Nix which paths are alive, and that traces
        the roots under the garbage collector lock, so `pynixd gc` without
        `--execute` still holds up a build for as long as the trace takes.

        Before planning, the pass re-walks the volatile roots fresh -- the
        living half no watch covers -- and vetoes them and their reference
        closure from the plan. A process that started after the last check
        must not lose its libraries to this pass. The veto is fail-closed:
        a closure the daemon cannot answer falls back to the seeds.

        The pass deletes in weight order, so a bounded pass frees the most
        with the fewest deletes, and a dry-run lists what goes first. The
        weight blends size and age by disk pressure: an empty disk collects
        oldest first, a full disk biggest first. `limit` takes the head of
        that order, and `target_usage` overrides the store's bound for one
        pass; both narrow only, and neither is plannable below zero.

        EXECUTE stays refused until the operator permits it. Planning asks
        Nix nothing it cannot already ask, but a delete is irreversible, and
        the planner's liveness answer is still unproven against the mirror:
        `gc_allow_execute` on the local store is the signature, off by
        default, and EXECUTE without it raises `GCNotPermittedError`.

        Progress travels on the wire when *client* is set: one `deleting`
        line per path, in the words `gc.cc:574` uses, plus the veto line
        and the outcome. The same lines buffer into the response, which is
        what a client that reads at the end prints.
        """
        if limit is not None and limit < 0:
            raise ValueError(f"limit deletes at most N paths, and {limit} is not a count")
        if target_usage is not None and target_usage <= 0:
            raise ValueError(f"target_usage bounds by a fraction, and {target_usage} is not one")
        if action == PynixdGCAction.EXECUTE and not getattr(self.ctx.local_store, "gc_allow_execute", False):
            log.warning("gc_execute_refused")
            raise GCNotPermittedError(
                "collector EXECUTE is not permitted: set gc_allow_execute "
                "after the liveness mirror shows sustained zero-divergence"
            )
        lines: list[LogNext] = []
        volatile = await run_sync(self._volatile_seeds)
        vetoed = await self._veto_closure(volatile)
        planned = await self.plan()
        spared = {str(path) for path in planned} & vetoed
        veto = LogNext(
            text=f"volatile veto: rechecked {len(volatile)} living roots, spared {len(spared)} planned paths"
        )
        lines.append(veto)
        if client is not None:
            await client.send(veto)
        log.info("gc_volatile_veto", fresh=len(volatile), spared=len(spared))
        weights = await self._weigh({path for path in planned if str(path) not in vetoed})
        if not weights:
            return self._answered(PynixdCollectGarbageResponse(store_paths=set(), bytes=0), lines)
        usage = self._disk_usage(self.ctx.local_store)
        if usage is None:
            # Unknown reads as half full: with no information neither axis
            # earns the lead. The bound still defaults to unbounded on top
            # of this, so a missing reading changes the order only, never
            # the set.
            log.warning("gc_no_disk_usage")
            pressure = 0.5
        else:
            used, total = usage
            pressure = used / total if total else 0.5
        ordered = _by_weight(weights, pressure)
        batch = self._bound_batch(self.ctx.local_store, ordered, weights, usage, target_usage)
        if limit is not None:
            batch = batch[:limit]
        if action != PynixdGCAction.EXECUTE:
            self._log_top(batch, weights, pressure)
            return self._answered(
                PynixdCollectGarbageResponse(
                    store_paths={StorePath(path) for path in batch},
                    bytes=sum(weights[path][0] for path in batch),
                ),
                lines,
            )
        return await self._delete(batch, weights, lines, client)

    @staticmethod
    def _answered(response: PynixdCollectGarbageResponse, lines: list[LogNext]) -> PynixdCollectGarbageResponse:
        """*response* carrying the pass's wire lines in its log buffer."""
        for line in lines:
            response.logs.add(line)
        return response

    def _volatile_seeds(self) -> set[str]:
        """The living roots, read synchronously for a worker thread.

        `/proc` and `temproots` move constantly and no watch covers them,
        so every pass reads them fresh just before planning. A store
        without a layout has no state to read, and vetoes nothing.
        """
        layout = getattr(self.ctx.local_store, "layout", None)
        if layout is None:
            return set()
        return walk_volatile(layout.state_dir, str(layout.store_dir))

    async def _veto_closure(self, volatile: set[str]) -> set[str]:
        """*volatile* plus what it references: the paths no pass may name.

        The daemon answers the closure without taking the collector lock,
        and a path the veto spares keeps whatever still names it, which is
        what keeps the delete set closed under referrers (`gc.cc:653`). A
        closure the daemon cannot answer -- no feature, a dropped seed --
        falls back to the seeds alone: sparing less than the full closure,
        but never nothing.
        """
        local = self.ctx.local_store
        if not volatile:
            return set()
        try:
            closed = {
                str(path)
                for path in (
                    await local.execute(QueryClosureRequest(paths={StorePath(path) for path in volatile}))
                ).paths
            }
        except Exception:
            log.warning("gc_veto_closure_failed", exc_info=True)
            return volatile
        if not volatile <= closed:
            log.warning("gc_veto_closure_incomplete", asked=len(volatile), answered=len(closed))
            return volatile
        return closed

    async def _weigh(self, paths: set[StorePath]) -> dict[str, tuple[int, int]] | None:
        """`(nar_size, age_seconds)` per planned path, or `None` when unknown.

        Sizes come from the path infos, ages from the access table; a path
        with no access row weighs age zero, which deprioritises it without
        excluding it. `None` fails the pass closed: a pass that cannot see
        the whole state deletes nothing, and shows nothing either.
        """
        if not paths:
            return {}
        local = self.ctx.local_store
        try:
            resp = await local.execute(QueryPathInfosRequest(paths=paths))
        except Exception:
            log.warning("gc_weigh_infos_failed", exc_info=True)
            return None
        sizes = {str(info.path): info.info.nar_size for info in resp.infos}
        query = getattr(getattr(local, "db", None), "query_access_times", None)
        if query is None:
            log.warning("gc_no_access_tracking")
            return None
        try:
            times = await query(set(sizes))
        except Exception:
            log.warning("gc_weigh_ages_failed", exc_info=True)
            return None
        if times is None:
            return None
        now = time.time()
        return {path: (size, max(0, int(now - times.get(path, now)))) for path, size in sizes.items()}

    def _log_top(
        self, ordered: list[str], weights: dict[str, tuple[int, int]], pressure: float, count: int = 10
    ) -> None:
        """The first planned paths, for the dry-run to show first.

        The response carries a set, which has no order, so the ranking
        travels in the logs instead: the journal keeps it, and the CLI
        prints what the daemon logged.
        """
        log.info(
            "gc_weights_top",
            pressure=round(pressure, 3),
            top=[{"path": path, "bytes": weights[path][0], "age_s": weights[path][1]} for path in ordered[:count]],
        )

    async def plan(self) -> set[StorePath]:
        """The paths that may leave the store.

        Two rules, picked by `gc_max_age` on the local store. `None` keeps
        the substituter rule below; a number of seconds picks the age rule,
        which needs no substituter: dead and unreferenced for that long goes,
        whether or not any cache holds it. Setting the number is the operator
        taking ownership of the store's old paths, and the default keeps the
        cache behaviour.
        """
        local = self.ctx.local_store
        max_age = getattr(local, "gc_max_age", None)
        if max_age is None:
            return await self._plan_deferred(local)
        return await self._plan_lru(local, max_age)

    async def _plan_deferred(self, local: Store) -> set[StorePath]:
        """The paths that may leave the store.

        A path stays when no substituter confirms it, and so does everything
        that path references. The drop set is therefore the complement of the
        reference closure of the unconfirmed paths, which makes it closed under
        referrers: a path unique to this store keeps the cached paths under it.

        Closed under referrers is also what makes the delete work at all.
        `gc.cc:653` refuses a named path whose referrer is not named in the
        same request, so the set travels together.

        Nix says which paths are alive, and this asks it rather than reading
        the roots itself. `LocalStore::findRuntimeRoots` (`gc.cc:332`) reads
        `/proc` of the whole machine, so a library a process of the host has
        mapped is a root of any store that keeps the `/nix/store` prefix.
        """
        stores = [store for store in self.ctx.stores.values() if store.gc_defer]
        if not stores:
            return set()

        all_paths: set[StorePath] = (await local.execute(QueryAllValidPathsRequest())).paths
        if not all_paths:
            return set()

        held: set[StorePath] = set()
        for store in stores:
            held |= await self._held_by(store, all_paths)

        unheld = all_paths - held
        keep: set[StorePath] = (await local.execute(QueryClosureRequest(paths=unheld))).paths
        if not unheld <= keep:
            # `DaemonStore.query_closure` answers an empty set for a store that
            # does not carry the feature, and the difference below would then
            # be the whole store.
            log.error("gc_closure_incomplete", asked=len(unheld), answered=len(keep))
            return set()

        live: set[StorePath] = (await local.call(_request(GCAction.RETURN_LIVE, set()))).paths_deleted
        droppable = all_paths - keep - live
        log.info("gc_plan", valid=len(all_paths), held=len(held), live=len(live), droppable=len(droppable))
        return droppable

    async def _plan_lru(self, local: Store, max_age: int) -> set[StorePath]:
        """The dead paths nothing referenced for `max_age` seconds.

        `PynixdPathAccess` says when pynixd last saw each path, and Nix says
        which paths are alive; the plan is the intersection. A path with no
        access row stays: "never seen" is not "seen long ago". The set is
        closed under referrers so Nix accepts it (`gc.cc:653`), which pulls
        in dead referrers even when they are fresh — deleting a path takes
        what still names it. A failure anywhere plans nothing: an LRU pass
        that cannot see the whole state deletes nothing.
        """
        all_paths: set[StorePath] = (await local.execute(QueryAllValidPathsRequest())).paths
        if not all_paths:
            return set()

        live: set[StorePath] = (await local.call(_request(GCAction.RETURN_LIVE, set()))).paths_deleted
        dead = {str(path) for path in all_paths} - {str(path) for path in live}

        stale = await self._stale_since(local, max_age)
        if stale is None:
            return set()
        seeds = dead & stale

        closed = await self._close_under_referrers(local, seeds)
        if closed is None:
            return set()
        droppable = {path for path in closed if path in dead}
        log.info(
            "gc_plan_lru",
            valid=len(all_paths),
            live=len(live),
            stale=len(stale),
            droppable=len(droppable),
        )
        return {StorePath(path) for path in droppable}

    async def _stale_since(self, local: Store, max_age: int) -> set[str] | None:
        """The paths unreferenced for `max_age` seconds, or `None` when unknown."""
        query = getattr(getattr(local, "db", None), "query_paths_not_referenced_since", None)
        if query is None:
            log.warning("gc_no_access_tracking")
            return None
        try:
            result = await query(max_age)
        except Exception:
            log.warning("gc_stale_query_failed", exc_info=True)
            return None
        if result is None:
            return None
        return {str(path) for path in result}

    async def _close_under_referrers(self, local: Store, seeds: set[str]) -> set[str] | None:
        """`seeds` plus everything that references them, transitively."""
        query = getattr(getattr(local, "db", None), "query_referrer_closure", None)
        if query is None:
            log.warning("gc_no_referrer_closure")
            return None
        try:
            return await query(seeds)
        except Exception:
            log.warning("gc_referrer_query_failed", exc_info=True)
            return None

    async def _held_by(self, store: Store, paths: set[StorePath]) -> set[StorePath]:
        """The paths of *paths* that *store* confirms it has.

        A store that cannot answer confirms nothing, so an unreachable
        substituter keeps every path it would have released.

        **`WantMassQuery` does not stop the sweep.** That flag asks a client
        that nobody configured to leave the cache alone, and `gc_defer` is an
        operator naming this store. `nix copy --to file://` writes `0` into
        every cache it makes, so honouring it here would make the flag useless
        for a lab cache. `HTTPBinaryCacheStore.query_valid_paths` asks for one
        narinfo at a time, so the sweep cannot flood a cache either way.
        """
        if is_http_binary_cache(store) and store.cache_info.get("WantMassQuery") == "0":
            log.debug("gc_store_asked_despite_wantmassquery", store_id=str(store.store_id))
        try:
            return (await store.execute(QueryValidPathsRequest(paths=paths))).paths
        except Exception:
            log.warning("gc_store_query_failed", store_id=str(store.store_id), exc_info=True)
            return set()

    async def _delete(
        self, batch: list[str], weights: dict[str, tuple[int, int]], lines: list[LogNext], client: ClientConn | None
    ) -> PynixdCollectGarbageResponse:
        """Delete *batch* through the daemon, narrating each path on the wire.

        One request carries the whole batch: the set is closed under
        referrers, and only a set that travels together passes the check at
        `gc.cc:653`. Each `deleting` line goes out before its path is asked
        for, the way `gc.cc:574` prints before it unlinks, so a refused
        batch leaves lines for paths that stayed. The outcome line says
        which one happened; the buffered lines travel in the response
        either way.
        """
        local = self.ctx.local_store
        # A pooled connection keeps a worker of the daemon alive, and that
        # worker holds a temporary root for every path that it took.
        await local.retire_idle_connections()

        if not batch:
            return self._answered(PynixdCollectGarbageResponse(store_paths=set(), bytes=0), lines)

        async def say(text: str) -> None:
            """One wire line, live when a client rides along, buffered always."""
            line = LogNext(text=text)
            lines.append(line)
            if client is not None:
                await client.send(line)

        for path in batch:
            await say(f"deleting '{path}'")
        try:
            resp = await local.call(
                _request(GCAction.DELETE_SPECIFIC, {StorePath(path) for path in batch}),
                raise_on_error=True,
            )
        except BackendError as exc:
            # `gc.cc:778` throws on the first live path and abandons the whole
            # request. The plan subtracts what Nix called live, so this is a
            # root that arrived after it. The next pass sees the new state.
            log.warning("gc_pass_refused", asked=len(batch), reason=str(exc))
            await say(f"delete refused: {exc}")
            return self._answered(PynixdCollectGarbageResponse(store_paths=set(), bytes=0), lines)

        left = set(batch) - {str(path) for path in resp.paths_deleted}
        if left:
            # The set is closed under referrers and Nix called none of it
            # alive, so a path left behind means a root arrived mid-pass.
            log.warning("gc_pass_partial", asked=len(batch), left=len(left))
            await say(f"delete partial: {len(left)} paths left behind")
        log.info("gc_pass_done", deleted=len(resp.paths_deleted), bytes=resp.bytes_freed)
        await say(f"deleted {len(resp.paths_deleted)} paths, {resp.bytes_freed} bytes freed")
        return self._answered(
            PynixdCollectGarbageResponse(store_paths=resp.paths_deleted, bytes=resp.bytes_freed), lines
        )

    def _bound_batch(
        self,
        local: Store,
        ordered: list[str],
        weights: dict[str, tuple[int, int]],
        usage: tuple[int, int] | None,
        target_override: float | None = None,
    ) -> list[str]:
        """The leading paths to delete: all planned, or down to the target.

        `gc_target_usage` bounds the pass by disk pressure: in weight order,
        stopping once the freed bytes project usage to or under the
        fraction. The hourly loop then relieves a full disk over several
        passes rather than emptying the plan at once. `None` keeps one
        unbounded pass. An unreadable usage keeps it too: the target cannot
        bind what nobody measured. A per-call override wins over the store
        for one pass.
        """
        target = target_override if target_override is not None else getattr(local, "gc_target_usage", None)
        if target is None or usage is None:
            return ordered
        used, total = usage
        batch = _take_until_below_target([(path, weights[path][0]) for path in ordered], used, total, target)
        log.info(
            "gc_bounded_pass",
            target=target,
            usage=used / total,
            batch=len(batch),
            planned=len(ordered),
        )
        return batch

    @staticmethod
    def _disk_usage(local: Store) -> tuple[int, int] | None:
        """`(used, total)` bytes of the filesystem holding the store."""
        try:
            directory = getattr(getattr(local, "layout", None), "real_store_dir", None)
            if directory is None:
                return None
            usage = shutil.disk_usage(directory)
            return (usage.used, usage.total)
        except Exception:
            log.warning("gc_disk_usage_failed", exc_info=True)
            return None


class LivenessWatch:
    """The cutover evidence, gathered one slow pass at a time.

    Each `check` refreshes the mirror in a worker thread -- the walks are
    sync filesystem reads and the closure is sqlite, so neither belongs on
    the event loop -- asks Nix what is alive, and logs the differential.
    Agreement is the gate: sustained empty differentials are what
    `gc_allow_execute` waits on. Nothing here plans or deletes; the
    expensive half is the question to Nix, which traces the roots under
    the garbage collector lock like any dry-run, and that is why the
    daemon runs this on `gc_liveness_interval`, not on the poll.
    """

    def __init__(self, tracker: RootsTracker, local: Store) -> None:
        self.tracker = tracker
        self.local = local

    @classmethod
    def from_local(cls, local: Store) -> LivenessWatch | None:
        """A watch over *local*, or `None` when it cannot host a mirror.

        The mirror lives in the store's own database beside Nix's tables,
        and reads the store's own roots directories: without a layout there
        is neither. `None` is not an error -- a store served without its
        state simply gathers no evidence.
        """
        layout = getattr(local, "layout", None)
        if layout is None:
            return None
        db_path = resolve_db_path(layout)
        if db_path is None:
            return None
        return cls(RootsTracker(layout.state_dir, str(layout.store_dir), db_path), local)

    async def check(self) -> bool:
        """Refresh the mirror, ask Nix, log the differential.

        True when they agree. The journal carries both outcomes -- an
        agreement is the evidence, and a divergence names its samples --
        so the cutover decision reads a log, not a dashboard.
        """
        live = await run_sync(self.tracker.refresh)
        theirs = await self._nix_live()
        only_mine, only_theirs = self.tracker.differential(theirs)
        if not only_mine and not only_theirs:
            log.info("gc_liveness_agreement", live=len(live))
            return True
        log.warning(
            "gc_liveness_divergence",
            live=len(live),
            only_tracker=len(only_mine),
            only_nix=len(only_theirs),
            tracker_sample=sorted(only_mine)[:10],
            nix_sample=sorted(only_theirs)[:10],
        )
        return False

    async def _nix_live(self) -> set[str]:
        """What Nix calls alive: the same trace a dry-run pays for."""
        resp = await self.local.call(_request(GCAction.RETURN_LIVE, set()))
        return {str(path) for path in resp.paths_deleted}
