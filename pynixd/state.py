"""Daemon state collection for `pynixd state` (issue #81).

One function per section, each a pure read over the context. Point-in-time
state reads its own sources: queue depth is a walk, sessions come from the
session registry, health from the stores. Lifetime totals come from the
metrics readers, which is the only place they exist. Unknown section names
are ignored, so an old daemon answers a new client's `wants` with what it
knows instead of failing the whole request.
"""

from __future__ import annotations

import gc
import tracemalloc
from typing import TYPE_CHECKING, Any

from . import metrics

if TYPE_CHECKING:
    from .context import PynixdContext

KNOWN_SECTIONS: tuple[str, ...] = ("queue", "stores", "sessions", "transfers", "totals", "benchmark")
"""Section names `collect` answers. New sections extend this tuple."""


def collect(ctx: PynixdContext, wants: list[str] | None = None) -> dict[str, Any]:
    """Collect the wanted sections, or every known section when empty."""
    wanted = set(wants or []) or set(KNOWN_SECTIONS)
    sections: dict[str, Any] = {}
    if "queue" in wanted:
        sections["queue"] = _queue_section(ctx)
    if "stores" in wanted:
        sections["stores"] = _stores_section(ctx)
    if "sessions" in wanted:
        sections["sessions"] = _sessions_section(ctx)
    if "transfers" in wanted:
        sections["transfers"] = _transfers_section(ctx)
    if "totals" in wanted:
        sections["totals"] = _totals_section()
    if "benchmark" in wanted:
        sections["benchmark"] = _benchmark_section()
    return sections


def _queue_section(ctx: PynixdContext) -> dict[str, Any]:
    """Builds by queue state. A walk, not the gauge: it cannot drift."""
    scheduler = ctx.scheduler
    if scheduler is None:
        return {"scheduler": False, "pending": 0, "building": 0, "done": 0}
    pending = building = done = 0
    for build in scheduler.queue.queue:
        if build.is_done:
            done += 1
        elif build.is_building:
            building += 1
        elif build.is_pending:
            pending += 1
    return {"scheduler": True, "pending": pending, "building": building, "done": done}


def _stores_section(ctx: PynixdContext) -> dict[str, Any]:
    """Configured stores by id: health and the systems each one serves."""
    stores: dict[str, Any] = {}
    for store_id, store in ctx.stores.items():
        matrix = store.feature_matrix or {}
        stores[str(store_id)] = {
            "healthy": bool(store.is_healthy),
            "systems": sorted(matrix.keys()),
        }
    return stores


def _sessions_section(ctx: PynixdContext) -> dict[str, Any]:
    """Live client sessions by transport."""
    return ctx.sessions.by_transport()


def _transfers_section(ctx: PynixdContext) -> dict[str, Any]:
    """NAR movement totals. Follows `metrics_enabled`: zeros when off.

    The per-store map does not: its sums are one `.inc()` per path from
    infos the transfer already walks, recorded unconditionally.
    """
    return {
        "bytes_received": metrics.nar_bytes_received_total(),
        "paths_received": metrics.nar_paths_received_total(),
        "bytes_served": metrics.nar_bytes_served_total(),
        "paths_served": metrics.nar_paths_served_total(),
        "stores": _store_transfers(ctx),
    }


def _store_transfers(ctx: PynixdContext) -> dict[str, Any]:
    """Per-store movement: NAR sums beside wire bytes, both directions."""
    store_ids = [str(store_id) for store_id in ctx.stores]
    nar = metrics.store_transfers(store_ids)
    wire = metrics.store_wire_bytes(store_ids)
    return {store_id: nar[store_id] | wire[store_id] for store_id in store_ids}


def _totals_section() -> dict[str, Any]:
    """Lifetime totals. Follows `metrics_enabled`: zeros when off."""
    return {
        "builds_completed": metrics.builds_completed_by_status(),
        "sessions_accepted": metrics.sessions_accepted_by_transport(),
    }


def _benchmark_section() -> dict[str, Any]:
    """Allocator census for the growth benchmark. Cheap when tracing is off.

    Production answers one boolean and runs no census. With `PYNIXD_BENCH=1`
    the daemon traces every allocation, and the harness reads current and
    peak bytes plus the live object count around each build.
    """
    if not tracemalloc.is_tracing():
        return {"tracing": False}
    current, peak = tracemalloc.get_traced_memory()
    return {
        "tracing": True,
        "tracemalloc_current": current,
        "tracemalloc_peak": peak,
        "gc_objects": len(gc.get_objects()),
    }
