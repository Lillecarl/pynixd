"""Daemon state collection for `pynixd state` (issue #81).

One function per section, each a pure read over the context, each answering
a model of `state_models`. Point-in-time state reads its own sources: queue
depth is a walk, sessions come from the session registry, health from the
stores. Lifetime totals come from the metrics readers, which is the only
place they exist. Unknown section names are ignored, so an old daemon answers
a new client's `wants` with what it knows instead of failing the whole
request.
"""

from __future__ import annotations

import gc
import time
import tracemalloc
from collections import Counter
from typing import TYPE_CHECKING

from . import metrics
from .state_models import (
    Benchmark,
    BuildRow,
    ClientRow,
    QueueCounts,
    StateReport,
    StoreRow,
    StoreTransfer,
    Totals,
    Transfers,
    UpstreamRow,
    WaitReason,
)

if TYPE_CHECKING:
    from nix_daemon_protocol.ids import RequestId

    from .build_queue import QueuedBuild
    from .context import PynixdContext
    from .scheduler import Scheduler

KNOWN_SECTIONS: tuple[str, ...] = (
    "queue",
    "builds",
    "stores",
    "sessions",
    "clients",
    "upstream",
    "transfers",
    "totals",
    "benchmark",
)
"""Section names `collect` answers. New sections extend this tuple."""


def collect(ctx: PynixdContext, wants: list[str] | None = None) -> StateReport:
    """Collect the wanted sections, or every known section when empty."""
    wanted = set(wants or []) or set(KNOWN_SECTIONS)
    return StateReport(
        queue=_queue_section(ctx) if "queue" in wanted else None,
        builds=_builds_section(ctx) if "builds" in wanted else None,
        stores=_stores_section(ctx) if "stores" in wanted else None,
        sessions=ctx.sessions.by_transport() if "sessions" in wanted else None,
        clients=_clients_section(ctx) if "clients" in wanted else None,
        upstream=upstream_in_flight(ctx) if "upstream" in wanted else None,
        transfers=_transfers_section(ctx) if "transfers" in wanted else None,
        totals=_totals_section() if "totals" in wanted else None,
        benchmark=_benchmark_section() if "benchmark" in wanted else None,
    )


def _queue_section(ctx: PynixdContext) -> QueueCounts:
    """Builds by queue state. A walk, not the gauge: it cannot drift."""
    scheduler = ctx.scheduler
    if scheduler is None:
        return QueueCounts(scheduler=False)
    return scheduler.queue.counts()


def _builds_section(ctx: PynixdContext) -> list[BuildRow]:
    """Every unfinished build, and why each pending one waits. Issue #92.

    The counts above said "one pending" for the build that held an IFD for
    hours in #89. This says which build, who wants it, and what holds it.
    """
    scheduler = ctx.scheduler
    if scheduler is None:
        return []
    queue = scheduler.queue
    now = time.monotonic()
    rows: list[BuildRow] = []
    for build in queue.queue:
        if build.is_done:
            continue
        wanters = queue.live_wanters(build)
        rows.append(
            BuildRow(
                id=int(build.build_id),
                drv=str(build.request.drv_path),
                state="building" if build.is_building else "pending",
                age_s=round(now - build.enqueued_at, 1),
                store=None if build.assigned_store_id is None else str(build.assigned_store_id),
                requests={int(r): queue.max_jobs_of(r) for r in sorted(wanters)},
                goal_holders=len(build.holders),
                subscribers=len(build.subscribers),
                retries=build.retries,
                building_s=None if build.started_at is None else round(now - build.started_at, 1),
                waits_for=None if build.is_building else pending_reason(scheduler, build, wanters),
            )
        )
    return rows


def pending_reason(scheduler: Scheduler, build: QueuedBuild, wanters: set[RequestId]) -> WaitReason:
    """What a pending build waits for."""
    if scheduler.queue.nobody_wants(build):
        return "unwanted"
    if wanters and not any(scheduler.request_has_a_local_slot(r) for r in wanters):
        return "local_slot"
    return "scheduling"


def pending_reasons(scheduler: Scheduler) -> Counter[WaitReason]:
    """Pending builds counted by `pending_reason`, for `pynixd_builds_waiting`."""
    queue = scheduler.queue
    return Counter(
        pending_reason(scheduler, build, queue.live_wanters(build)) for build in queue.queue if build.is_pending
    )


def _clients_section(ctx: PynixdContext) -> list[ClientRow]:
    """Every live session, and the operation it runs now. Issue #92."""
    now = time.monotonic()
    rows = [
        ClientRow(
            transport=proxy.transport,
            user=proxy.username,
            connected_s=round(now - proxy.connected_at, 1),
            op=None if proxy.current_op is None else proxy.current_op[0],
            op_s=None if proxy.current_op is None else round(now - proxy.current_op[1], 1),
        )
        for proxy in ctx.sessions.proxies()
    ]
    return sorted(rows, key=lambda row: -(row.op_s or 0))


def upstream_in_flight(ctx: PynixdContext) -> list[UpstreamRow]:
    """Every operation waiting on a store, and how long that store is silent.

    A GC or a build can be silent for a long time and be right, so pynixd
    cuts none of them off; this is where a stalled one shows (#93).
    """
    now = time.monotonic()
    rows: list[UpstreamRow] = []
    for store_id, store in ctx.stores.items():
        pool = getattr(store, "pool", None)
        if pool is None:
            continue
        for conn in pool.waiting_on_upstream():
            op = conn.current_op
            if op is None:
                continue
            rows.append(
                UpstreamRow(
                    store=str(store_id),
                    conn=conn.id,
                    op=op[0],
                    op_s=round(now - op[1], 1),
                    silent_s=round(now - max(op[1], conn.r.last_read_at), 1),
                )
            )
    return sorted(rows, key=lambda row: -row.silent_s)


def _stores_section(ctx: PynixdContext) -> dict[str, StoreRow]:
    """Configured stores by id: health and the systems each one serves."""
    return {
        str(store_id): StoreRow(healthy=bool(store.is_healthy), systems=sorted((store.feature_matrix or {}).keys()))
        for store_id, store in ctx.stores.items()
    }


def _transfers_section(ctx: PynixdContext) -> Transfers:
    """NAR movement totals. Follows `metrics_enabled`: zeros when off.

    The per-store map does not: its sums are one `.inc()` per path from
    infos the transfer already walks, recorded unconditionally.
    """
    store_ids = [str(store_id) for store_id in ctx.stores]
    nar = metrics.store_transfers(store_ids)
    wire = metrics.store_wire_bytes(store_ids)
    return Transfers(
        bytes_received=metrics.nar_bytes_received_total(),
        paths_received=metrics.nar_paths_received_total(),
        bytes_served=metrics.nar_bytes_served_total(),
        paths_served=metrics.nar_paths_served_total(),
        stores={store_id: StoreTransfer(**nar[store_id], **wire[store_id]) for store_id in store_ids},
    )


def _totals_section() -> Totals:
    """Lifetime totals. Follows `metrics_enabled`: zeros when off."""
    return Totals(
        builds_completed=metrics.builds_completed_by_status(),
        sessions_accepted=metrics.sessions_accepted_by_transport(),
    )


def _benchmark_section() -> Benchmark:
    """Allocator census for the growth benchmark. Cheap when tracing is off.

    Production answers one boolean and runs no census. With `PYNIXD_BENCH=1`
    the daemon traces every allocation, and the harness reads current and
    peak bytes plus the live object count around each build.
    """
    if not tracemalloc.is_tracing():
        return Benchmark(tracing=False)
    current, peak = tracemalloc.get_traced_memory()
    return Benchmark(
        tracing=True,
        tracemalloc_current=current,
        tracemalloc_peak=peak,
        gc_objects=len(gc.get_objects()),
    )
