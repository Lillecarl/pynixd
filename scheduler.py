"""
Scheduler for build distribution.

Runs scheduling passes triggered by:
- New builds enqueued
- Build completes (slot opens)
- Path transfer completes (availability changes)

DAG-aware: builds are only schedulable when all input_srcs are present
in the local store. If inputs are missing but available on a remote store,
they are pulled automatically.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import anyio
import structlog
from anyio import to_thread

from . import metrics
from .allocator import BuildAllocator, RankedStore, TelemetryStoreRanker
from .build_queue import BuildQueue, QueuedBuild
from .exceptions import BackendError, InfrastructureError, ResourceExhaustedError
from .serde import LogNext, QueryValidPathsRequest
from .serde import StorePath as SerdeStorePath
from .store import DaemonStore, LocalDBStore
from .store.transfer import stream_paths_store_to_store
from .store_path import StorePath
from .substitution_queue import SubstitutionQueue

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .connection import Connection
    from .context import PynixdContext
    from .derived_path import DerivedPath
    from .serde import (
        BuildDerivationRequest,
        BuildDerivationResponse,
    )
    from .serde.aliases import StorePathSet
    from .serde.ids import BuildId, RequestId, StoreId

log = structlog.get_logger(__name__)


class Scheduler:
    """Schedules builds across stores based on locality and DAG deps."""

    def __init__(
        self,
        ctx: PynixdContext,
    ) -> None:
        self.ctx = ctx
        self.queue = BuildQueue()
        self.substitution_queue = SubstitutionQueue(ctx)
        self.local_store = ctx.local_store
        self._dynamic_feature_matrix: dict[str, set[str]] = {}

        self.ranker = TelemetryStoreRanker(ctx.settings)
        self.allocator = BuildAllocator(self.stores, self.local_store, self.ranker)
        self.trigger_event = anyio.Event()
        self.running = False

    @property
    def stores(self) -> Mapping[StoreId, DaemonStore]:
        return {
            store_id: store
            for store_id, store in self.ctx.stores.items()
            if isinstance(store, DaemonStore) and not store.no_schedule
        }

    @property
    def dynamic_feature_matrix(self) -> dict[str, set[str]]:
        return self._dynamic_feature_matrix

    def add_dynamic_feature(self, system: str, feature: str = "") -> None:
        """Register that a system+feature combo may become available.

        Used by Kubernetes controllers or config to declare that builders
        for a given platform exist, even if no store is currently connected.
        Builds requiring these features will queue instead of failing.
        """
        self._dynamic_feature_matrix.setdefault(system, set())
        if feature:
            self._dynamic_feature_matrix[system].add(feature)
        self.trigger()

    def add_dynamic_features(self, feature_matrix: dict[str, set[str]]) -> None:
        """Merge an entire feature_matrix into the dynamic matrix."""
        for system, features in feature_matrix.items():
            self._dynamic_feature_matrix.setdefault(system, set())
            self._dynamic_feature_matrix[system] |= features
        self.trigger()

    def trigger(self) -> None:
        """Signal that a scheduling pass is needed."""
        self.trigger_event.set()

    def on_store_added(self, store: DaemonStore, dynamic: bool = False) -> None:
        """Hook called by Server when a new store is added.

        If dynamic=True, the store's feature_matrix is also registered in
        the dynamic_feature_matrix so that builds for this platform continue
        to queue even after the store is removed.
        """
        log.info("store_added_to_scheduler", store_id=store.store_id, dynamic=dynamic)
        if dynamic and store.feature_matrix:
            self.add_dynamic_features(store.feature_matrix)
        self.trigger()

    def _dynamic_supports(self, system: str, features: set[str] | None = None) -> bool:
        """Check if the dynamic_feature_matrix declares support for a system+features combo."""
        sys_features = self._dynamic_feature_matrix.get(system)
        if sys_features is None:
            return False
        if not features:
            return True
        return features.issubset(sys_features)

    async def drain_store(self, store_id: StoreId, drain_timeout: float = 300.0) -> None:
        """Gracefully drain and stop using a store for new builds."""
        store = self.ctx.stores.get(store_id)
        if not isinstance(store, DaemonStore):
            return

        log.info(
            "draining_store_in_scheduler",
            store_id=store_id,
            drain_timeout=drain_timeout,
        )
        store.draining = True
        self.trigger()

        start = time.monotonic()
        while time.monotonic() - start < drain_timeout:
            if store.in_flight == 0:
                break
            await anyio.sleep(1.0)
        else:
            log.warning("drain_timeout_reached", store_id=store_id)

        pending = await self.queue.get_pending()
        for build in pending:
            if build.assigned_store_id == store_id:
                if build.build_task and not build.build_task.done():
                    log.info(
                        "cancelling_build_for_store_removal",
                        build_id=build.build_id,
                        store_id=store_id,
                    )
                    build.build_task.cancel()
                build.reset_for_retry(store_id)

        self.trigger()

    async def build_derivation(
        self,
        request: BuildDerivationRequest,
        scheduler_request_id: RequestId | None = None,
        derived_paths_for_request: set[DerivedPath] | None = None,
        from_goal_path: bool = False,
    ) -> tuple[BuildId, asyncio.Future[BuildDerivationResponse]]:
        """Add a build to the queue and trigger the scheduler."""
        t0 = time.monotonic()
        hint = None
        if isinstance(self.local_store, LocalDBStore):
            pname = request.derivation.env.get("pname", None)
            if pname:
                hint = await self.local_store.db.get_build_stats_hint(
                    pname,
                    request.derivation.platform,
                )
        t_hint = time.monotonic()

        res = await self.queue.enqueue(
            request,
            expected_duration=hint,
            scheduler_request_id=scheduler_request_id,
            derived_paths_for_request=derived_paths_for_request,
            from_goal_path=from_goal_path,
        )
        t_enqueue = time.monotonic()
        self.trigger()

        log.debug(
            "build_derivation_timing",
            drv=str(request.drv_path),
            hint=f"{t_hint - t0:.3f}s",
            enqueue=f"{t_enqueue - t_hint:.3f}s",
            total=f"{t_enqueue - t0:.3f}s",
        )
        return res

    async def start(self) -> None:
        """Start the scheduler loop."""
        self.running = True
        log.info("scheduler_started")
        while self.running:
            try:
                await self.trigger_event.wait()
                self.trigger_event = anyio.Event()
                await self.schedule()
                await anyio.sleep(0.01)
            except anyio.get_cancelled_exc_class():
                break
            except Exception:
                log.exception("scheduler_pass_failed")
                await anyio.sleep(1.0)

    async def stop(self) -> None:
        """Stop the scheduler and cancel all pending builds."""
        self.running = False
        self.trigger()

    async def close(self) -> None:
        """Alias for stop() for consistency."""
        await self.substitution_queue.close()
        await self.stop()

    async def schedule(self) -> None:
        """The core scheduling logic.

        1. Populate metadata for builds that need it.
        2. Filter schedulable vs waiting builds.
        3. Assign schedulable builds to stores.
        4. Update store metrics.
        """
        pending = await self.queue.get_pending()
        if not pending:
            self._update_store_metrics()
            return

        schedulable, override_in_flight = self._filter_schedulable(pending)

        waiting_slot = await self._assign_to_stores(schedulable, override_in_flight)

        self._update_store_metrics()

        log.debug(
            "scheduling_pass_done",
            pending=len(pending),
            waiting_slot=len(waiting_slot),
            in_flight={s.store_id: s.in_flight for s in self.stores.values()},
            cpu_util={
                s.store_id: f"{s.cpu_util.utilization:.1f}%" if s.cpu_util else None for s in self.stores.values()
            },
        )

    def _filter_schedulable(
        self,
        pending: list[QueuedBuild],
    ) -> tuple[list[QueuedBuild], dict[StoreId, int]]:
        """Triage pending builds into schedulable.

        Returns (schedulable, override_in_flight).
        override_in_flight accounts for builds assigned this cycle but not yet
        reflected in ``store.in_flight``.
        """
        schedulable: list[QueuedBuild] = []

        # Count builds that are already building per store (may exceed
        # store.in_flight because the counter hasn't been updated yet).
        assigned_count: dict[StoreId, int] = {s.store_id: 0 for s in self.stores.values()}
        for build in pending:
            if build.is_building and build.assigned_store_id:
                assigned_count[build.assigned_store_id] += 1

        # Use the max of the store's reported in_flight and our assigned_count
        # to avoid undercounting during rapid scheduling passes.
        override_in_flight: dict[StoreId, int] = {
            s.store_id: max(s.in_flight, assigned_count[s.store_id]) for s in self.stores.values()
        }

        for build in pending:
            if build.is_building:
                continue

            # All builds are schedulable; missing inputs are
            # discovered and pulled during execution.
            schedulable.append(build)

        return schedulable, override_in_flight

    async def _assign_to_stores(
        self,
        schedulable: list[QueuedBuild],
        override_in_flight: dict[StoreId, int],
    ) -> list[QueuedBuild]:
        """Assign schedulable builds to backends.

        Handles tiny-build fast-track, standard remote assignment, and
        permanent failure for builds with no compatible store.

        Re-checks build state after ranking to avoid TOCTOU races.
        Returns builds that are waiting for a slot (no store available).
        """
        waiting_slot: list[QueuedBuild] = []
        assigned_this_pass: dict[StoreId, int] = {}

        for build in schedulable:
            build_features = build.request.derivation.effective_required_features

            # Check if another pass already assigned this build
            if build.is_building:
                continue

            # Standard remote backend assignment
            ranked = self.allocator.rank_stores(
                build,
                assigned_this_pass,
                override_in_flight=override_in_flight,
            )

            local_store = self.local_store
            local_is_fallback = (
                not ranked
                and local_store.is_healthy
                and not local_store.draining
                and not build.is_blacklisted(local_store.store_id)
                and local_store.supports_derivation(build.request.derivation.platform, build_features)
            )

            if not ranked and not local_is_fallback and not self._has_compatible_store(build, build_features):
                await self._fail_no_compatible_store(build, build_features)
                continue

            if ranked or local_is_fallback:
                rs = next(iter(ranked)) if ranked else RankedStore(local_store.store_id, 0.0, local_store)
                log.debug(
                    "build_assigned_to_store",
                    build_id=build.build_id,
                    store_id=rs.store_id,
                    score=rs.score,
                )
                metrics.QUEUE_SIZE.labels(status="pending").dec()
                metrics.QUEUE_SIZE.labels(status="building").inc()
                build.build_task = asyncio.create_task(
                    self.execute_build(build, rs.store),
                )
                assigned_this_pass[rs.store_id] = assigned_this_pass.get(rs.store_id, 0) + 1
            else:
                # All compatible stores are busy, or this build can't be placed
                all_compatible = [
                    s
                    for s in self.stores.values()
                    if s.supports_derivation(build.request.derivation.platform, build_features)
                ]
                if all_compatible and all(build.is_blacklisted(s.store_id) for s in all_compatible):
                    await self._fail_all_compatible_blacklisted(build, build_features, all_compatible)
                    continue
                waiting_slot.append(build)

        return waiting_slot

    def _has_compatible_store(
        self,
        build: QueuedBuild,
        build_features: set[str] | None,
    ) -> bool:
        """Check if any live or dynamic store could ever support this build."""
        all_stores = list(self.stores.values())
        return any(
            s.store_id != self.local_store.store_id
            and s.supports_derivation(build.request.derivation.platform, build_features)
            for s in all_stores
        ) or self._dynamic_supports(build.request.derivation.platform, build_features)

    async def _fail_no_compatible_store(
        self,
        build: QueuedBuild,
        build_features: set[str] | None,
    ) -> None:
        """Permanently fail a build with no compatible store."""
        error_msg = f"No compatible store for {build.request.derivation.platform}" + (
            f" (requires {', '.join(sorted(build_features))})" if build_features else ""
        )
        await self.queue.fail(build.build_id, error_msg)
        for line in error_msg.split("\n"):
            await build.post_log_and_fanout(LogNext(text=f"pynixd: {line}\n"))

    async def _fail_all_compatible_blacklisted(
        self,
        build: QueuedBuild,
        build_features: set[str] | None,
        compatible: list[DaemonStore],
    ) -> None:
        """Permanently fail a build blacklisted by all compatible stores."""
        failed_ids = [s.store_id for s in compatible]
        error_msg = (
            f"All compatible stores failed for {build.request.derivation.platform}"
            + (f" (requires {', '.join(sorted(build_features))})" if build_features else "")
            + f": {', '.join(failed_ids)}"
        )
        await self.queue.fail(build.build_id, error_msg)
        for line in error_msg.split("\n"):
            await build.post_log_and_fanout(LogNext(text=f"pynixd: {line}\n"))

    def _update_store_metrics(self) -> None:
        """Update per-store metrics."""
        for s in self.stores.values():
            metrics.STORE_HEALTHY.labels(store_id=s.store_id).set(
                1 if s.is_healthy else 0,
            )
            if s.cpu_util:
                metrics.STORE_CPU_UTILIZATION.labels(store_id=s.store_id).set(
                    s.cpu_util.utilization,
                )

    async def validate_known_paths(self, paths: StorePathSet) -> None:
        """Query paths against the local store via QueryValidPaths."""
        if not paths:
            return
        try:
            await self.local_store.execute(
                QueryValidPathsRequest(
                    paths={SerdeStorePath(path=str(path)) for path in paths},  # pyright: ignore[reportUnhashable]
                    substitute=0,
                ),
            )
        except (BackendError, OSError, ConnectionError):
            log.exception("validate_known_paths_failed", count=len(paths))

    async def execute_build(self, build: QueuedBuild, store: DaemonStore) -> None:
        """Execute build on a store, handling inputs and outputs.

        Internal phases:
        1. _prepare_build  — CA registration, resolve, strip features, stream inputs
        2. _execute        — Call backend via build_conn
        3. _collect_outputs — Pull outputs, register realisations, record stats
        """
        build.assigned_store_id = store.store_id
        build_resp: BuildDerivationResponse | None = None
        completed = False
        try:
            async with store.build_conn() as conn:
                await self._prepare_build(build, store, conn)
                await build.post_log_and_fanout(
                    LogNext(text=f"pynixd: starting build on {store.store_id} at {datetime.now(UTC).isoformat()}\n")
                )
                build_resp = await self._execute(build, store, conn)
                if build_resp.result.status == 0:
                    await self.queue.complete(build.build_id, build_resp)
                    completed = True
                    self.trigger()
                    try:
                        await self._collect_outputs(build, store, conn, build_resp)
                    except Exception:
                        log.exception("output_pull_failed_after_build_response", build_id=build.build_id)

        except ResourceExhaustedError as e:
            log.info(
                "build_deferred_busy",
                build_id=build.build_id,
                store_id=store.store_id,
                reason=str(e),
            )
            build.reset_for_busy()
            self.trigger()
        except (BackendError, InfrastructureError) as e:
            log.warning("build_failed_retryable", build_id=build.build_id, error=str(e))
            await build.post_log_and_fanout(LogNext(text=f"pynixd: build failed on {store.store_id}, retrying: {e}\n"))
            build.reset_for_retry(store.store_id)
            self.trigger()
        except Exception:
            log.exception("build_crashed", build_id=build.build_id)
            await build.post_log_and_fanout(LogNext(text="pynixd: internal scheduler error, failing build\n"))
            await self.queue.fail(build.build_id, "Internal scheduler error")
            self.trigger()

        if build_resp is not None and not completed:
            await self.queue.complete(build.build_id, build_resp)
            self.trigger()

    async def _prepare_build(
        self,
        build: QueuedBuild,
        store: DaemonStore,
        conn: Connection,  # build connection (opaque to this method)
    ) -> None:
        """Register CA realisations, resolve deferred derivations, stream inputs.

        All operations that must happen before the build request is sent
        to the backend daemon.
        """
        # Strip pynixd-handled features from requiredSystemFeatures
        self.allocator.strip_handled_features(build)

        # 1. Filter inputs already present on the builder store
        input_srcs = build.request.derivation.input_srcs
        if input_srcs:
            paths_to_check = {SerdeStorePath(path=str(p)) for p in input_srcs}  # pyright: ignore[reportUnhashable]
            try:
                check = await store.execute(
                    QueryValidPathsRequest(
                        paths=paths_to_check,
                        substitute=0,
                    )
                )
                valid_paths = [str(cp) for cp in check.paths]
                known = [str(p) for p in input_srcs if str(p) in valid_paths]
            except (BackendError, OSError, ConnectionError):
                known = []
            missing_paths = {StorePath(str(p)) for p in input_srcs if str(p) not in known}
        else:
            missing_paths = set()
        if missing_paths:
            log.debug(
                "build_sending_inputs",
                build_id=build.build_id,
                store_id=store.store_id,
                count=len(missing_paths),
            )
            await stream_paths_store_to_store(
                self.local_store,
                store,
                missing_paths,
            )

    async def _execute(
        self,
        build: QueuedBuild,
        store: DaemonStore,
        conn: Connection,
    ) -> BuildDerivationResponse:
        """Call the backend daemon and return the response.

        Sends the request via conn.call() without a client. Stderr is
        buffered in the response, then fanned out to the build's subscribers.
        """
        log.debug("build_executing", build_id=build.build_id, store_id=store.store_id)
        build.started_at = time.monotonic()
        if build.wait_time is not None:
            metrics.QUEUE_WAIT_DURATION.observe(build.wait_time)

        resp = await conn.call(build.request)
        if resp.logs.messages:
            for msg in resp.logs.messages:
                await build.post_log_and_fanout(msg)
        if resp.result.status != 0 and resp.result.error_msg:
            for line in resp.result.error_msg.split("\n"):
                await build.post_log_and_fanout(LogNext(text=f"pynixd: {line}\n"))
        log.debug(
            "build_executed",
            build_id=build.build_id,
            status=resp.result.status,
        )
        return resp

    async def _collect_outputs(
        self,
        build: QueuedBuild,
        store: DaemonStore,
        conn: Connection,  # build connection
        resp: BuildDerivationResponse,
    ) -> None:
        """Pull outputs back to local store, register realisations, record stats.

        Runs after a successful build execution.
        """
        if resp.result.status != 0:
            return

        # Pull outputs from remote store to local store
        ca_output_paths: StorePathSet = set()
        if resp.result.built_outputs:
            for realisation in resp.result.built_outputs.values():
                out_path = StorePath(str(realisation.out_path)) if realisation.out_path else StorePath("")
                if out_path:
                    ca_output_paths.add(
                        out_path.with_store_prefix(),
                    )
            build.ca_realisations = list(resp.result.built_outputs.values())

        outputs = build.request.derivation.output_paths()
        static_paths = {p for p in outputs.values() if p != StorePath("")}
        all_output_paths = static_paths | ca_output_paths
        for path in all_output_paths:
            self.ctx.output_locations[str(path)] = store.store_id
        log.info(
            "pulling_paths",
            store_id=store.store_id,
            count=len(all_output_paths),
        )

        log.debug(
            "output_paths_left_to_frontend_daemon",
            count=len(all_output_paths),
            store_id=store.store_id,
        )
        await self._direct_import_localdb_outputs(store, all_output_paths)

        # Record build statistics
        if isinstance(self.local_store, LocalDBStore):
            pname = build.request.derivation.env.get("pname")
            if pname:
                started_at = build.started_at
                if started_at is not None:
                    duration = int((time.monotonic() - started_at) * 1000)
                    await self.local_store.db.record_build_stats(
                        pname=pname,
                        platform=build.request.derivation.platform,
                        derivation_json=build.request.derivation.to_stats_json(),
                        cpu_user_us=resp.result.cpu_user.value if resp.result.cpu_user else None,
                        cpu_system_us=resp.result.cpu_system.value if resp.result.cpu_system else None,
                        duration_ms=duration,
                    )
                    expected = build.expected_duration
                    log.info(
                        "build_stats_recorded",
                        pname=pname,
                        platform=build.request.derivation.platform,
                        expected_ms=expected,
                        actual_ms=duration,
                        error_pct=f"{(duration - expected) / expected * 100:.1f}" if expected else None,
                    )

    async def _wait_for_local_paths(
        self,
        paths: StorePathSet,
        delay: float = 2.0,
    ) -> StorePathSet:
        """Wait briefly for the frontend daemon's own output import."""
        if not paths:
            return set()
        if not isinstance(self.local_store, LocalDBStore):
            log.debug(
                "skip_wait_for_local_paths_without_db",
                count=len(paths),
                store_id=self.local_store.store_id,
            )
            return set()

        deadline = time.monotonic() + delay
        while True:
            try:
                resp = await self.local_store.query_valid_paths(
                    QueryValidPathsRequest(
                        paths={SerdeStorePath(path=str(path)) for path in paths},  # pyright: ignore[reportUnhashable]
                        substitute=0,
                    ),
                )
                valid = {StorePath(str(path)) for path in resp.paths}
                if valid >= paths or time.monotonic() >= deadline:
                    return valid
            except (BackendError, OSError, ConnectionError):
                if time.monotonic() >= deadline:
                    return set()
                log.exception("wait_for_local_paths_failed", count=len(paths))

            await anyio.sleep(0.05)

    async def _direct_import_localdb_outputs(
        self,
        store: DaemonStore,
        paths: StorePathSet,
    ) -> None:
        if not paths or not isinstance(store, LocalDBStore) or not isinstance(self.local_store, LocalDBStore):
            return
        if store.store_path is None or self.local_store.store_path is None:
            return

        for path in paths:
            src = store.store_path / str(path).lstrip("/")
            dst = self.local_store.store_path / str(path).lstrip("/")
            if not src.exists() and not src.is_symlink():
                log.warning("direct_output_import_missing_source", path=str(path), store_id=store.store_id)
                continue
            if not dst.exists() and not dst.is_symlink():
                await to_thread.run_sync(self._copy_store_path, src, dst)

        paths_json = json.dumps([str(path) for path in paths])
        async with store.db.acquire_conn() as src_db, self.local_store.db.acquire_conn() as dst_db:
            rows_cursor = await src_db.execute(
                """
                    SELECT path, hash, registrationTime, narSize, deriver, ultimate, sigs, ca
                    FROM ValidPaths
                    WHERE path IN (SELECT value FROM json_each(?))
                    """,
                (paths_json,),
            )
            rows = await rows_cursor.fetchall()
            for row in rows:
                await dst_db.execute(
                    """
                        INSERT OR IGNORE INTO ValidPaths
                            (path, hash, registrationTime, narSize, deriver, ultimate, sigs, ca)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                    row,
                )

            refs_cursor = await src_db.execute(
                """
                    SELECT referrer.path, reference.path
                    FROM Refs r
                    JOIN ValidPaths referrer ON r.referrer = referrer.id
                    JOIN ValidPaths reference ON r.reference = reference.id
                    WHERE referrer.path IN (SELECT value FROM json_each(?))
                    """,
                (paths_json,),
            )
            refs = await refs_cursor.fetchall()
            for referrer, reference in refs:
                await dst_db.execute(
                    """
                        INSERT OR IGNORE INTO Refs (referrer, reference)
                        SELECT referrer.id, reference.id
                        FROM ValidPaths referrer, ValidPaths reference
                        WHERE referrer.path = ? AND reference.path = ?
                        """,
                    (referrer, reference),
                )
            await dst_db.commit()

        log.info("direct_output_import_complete", count=len(paths), store_id=store.store_id)

    @staticmethod
    def _copy_store_path(src, dst) -> None:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_symlink():
            dst.symlink_to(src.readlink())
        elif src.is_dir():
            shutil.copytree(src, dst, symlinks=True)
        else:
            shutil.copy2(src, dst)
