"""The collector behind `PynixdCollectGarbage` (op 101).

One rule today: a path may leave the local store when a substituter that
`gc_defer` names already holds it, and when Nix agrees that it is not alive.

**The rule is not liveness alone.** The store pynixd serves is a cache, and
almost nothing in it is rooted, so `GCAction.DELETE_DEAD` would empty it. This
collector names every path that it deletes.

Age is the second rule, and it is here: `gc_max_age` on the local store
plans the dead paths nothing referenced for that many seconds, with no
substituter needed. Size is the next rule, and it is not here.
`LocalStoreDB` already records when each path was last referenced, and
issue #18 holds the size work.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from nix_daemon_protocol import (
    CollectGarbageRequest,
    GCAction,
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
from .exceptions import BackendError
from .store import is_http_binary_cache

if TYPE_CHECKING:
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


class Collector:
    """Chooses what leaves the local store, and asks Nix to delete it."""

    def __init__(self, ctx: PynixdContext) -> None:
        self.ctx = ctx

    async def run(self, action: PynixdGCAction) -> PynixdCollectGarbageResponse:
        """Plan a pass, and run it when *action* says to.

        A plan is not free. It asks Nix which paths are alive, and that traces
        the roots under the garbage collector lock, so `pynixd gc` without
        `--execute` still holds up a build for as long as the trace takes.
        """
        paths = await self.plan()
        if action != PynixdGCAction.EXECUTE or not paths:
            return PynixdCollectGarbageResponse(store_paths=paths, bytes=await self._size(paths))
        return await self._delete(paths)

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

    async def _size(self, paths: set[StorePath]) -> int:
        if not paths:
            return 0
        resp = await self.ctx.local_store.execute(QueryPathInfosRequest(paths=paths))
        return sum(info.info.nar_size for info in resp.infos)

    async def _delete(self, paths: set[StorePath]) -> PynixdCollectGarbageResponse:
        local = self.ctx.local_store
        # A pooled connection keeps a worker of the daemon alive, and that
        # worker holds a temporary root for every path that it took.
        await local.retire_idle_connections()

        try:
            resp = await local.call(_request(GCAction.DELETE_SPECIFIC, paths), raise_on_error=True)
        except BackendError as exc:
            # `gc.cc:778` throws on the first live path and abandons the whole
            # request. The plan subtracts what Nix called live, so this is a
            # root that arrived after it. The next pass sees the new state.
            log.warning("gc_pass_refused", asked=len(paths), reason=str(exc))
            return PynixdCollectGarbageResponse(store_paths=set(), bytes=0)

        left = paths - resp.paths_deleted
        if left:
            # The set is closed under referrers and Nix called none of it
            # alive, so a path left behind means a root arrived mid-pass.
            log.warning("gc_pass_partial", asked=len(paths), left=len(left))
        log.info("gc_pass_done", deleted=len(resp.paths_deleted), bytes=resp.bytes_freed)
        return PynixdCollectGarbageResponse(store_paths=resp.paths_deleted, bytes=resp.bytes_freed)
