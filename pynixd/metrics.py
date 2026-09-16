"""Prometheus metrics for pynixd build orchestration.

Provides Gauges, Counters, and Histograms to monitor queue depth,
store load, and build throughput. These are exposed via the
PynixdHttpServer's /metrics endpoint.
"""

from __future__ import annotations

import os

import structlog
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from prometheus_client.core import GaugeMetricFamily

from nix_daemon_protocol.store_dir import real_store_dir

log = structlog.get_logger(__name__)

# --- Queue Metrics ---

QUEUE_SIZE = Gauge(
    "pynixd_build_queue_size",
    "Number of builds currently in the queue",
    ["status"],  # pending, building, done
)

BUILD_DURATION = Histogram(
    "pynixd_build_duration_seconds",
    "Time spent actively building (excluding queue wait time)",
    buckets=(1, 5, 15, 30, 60, 120, 300, 600, 1200, 3600),
)

QUEUE_WAIT_DURATION = Histogram(
    "pynixd_build_queue_wait_duration_seconds",
    "Time spent in the queue before a build task starts",
    buckets=(1, 5, 10, 30, 60, 120, 300),
)

BUILDS_COMPLETED = Counter(
    "pynixd_builds_completed_total",
    "Total number of builds completed",
    ["status"],  # success, failure
)

# --- Store Metrics ---

STORE_CPU_UTILIZATION = Gauge(
    "pynixd_store_cpu_utilization_percent",
    "Reported CPU utilization of a backend store",
    ["store_id"],
)


STORE_HEALTHY = Gauge(
    "pynixd_store_healthy",
    "Health status of a backend store (1 = healthy, 0 = unhealthy)",
    ["store_id"],
)


class StoreSpaceCollector:
    """How much room the store has, read when a scrape asks for it.

    A collector, and not a `Gauge` that something keeps up to date. The answer
    costs one `statvfs` and nothing else in pynixd needs it, so reading it on
    the scrape is both cheaper and fresher than a periodic write.

    **This is the file system that holds the store, and not the size of the
    store.** The two are not the same number and the difference is large:
    measured on this machine, `sum(narSize)` over `ValidPaths` answers 952 GB
    for a file system of 268 GB. NAR sizes add up without the deduplication
    and the hard links that the store on disk has, so that sum answers a
    question nobody asked.

    It is also too slow to serve. `select count(*), sum(narSize)` took 584 ms
    over 141,749 paths, against 0.010 ms for the `statvfs` here. A scrape
    every 15 s cannot pay half a second.
    """

    def collect(self):
        """Yield the two numbers, or nothing when the store is unreachable."""
        path = real_store_dir()
        try:
            stat = os.statvfs(path)
        except OSError:
            # A store directory that is gone or unreadable is not a reason to
            # fail a scrape: every other metric in the registry is still an
            # answer. Absent series read as absent on a dashboard, which is
            # what this is.
            log.warning("store_space_unreadable", path=path, exc_info=True)
            return

        yield GaugeMetricFamily(
            "pynixd_store_filesystem_size_bytes",
            "Total size of the file system that holds the store directory",
            value=stat.f_blocks * stat.f_frsize,
        )
        # `f_bavail` and not `f_bfree`: the reserved blocks are not available
        # to pynixd, and a dashboard that reads `f_bfree` says there is room
        # where a build would fail.
        yield GaugeMetricFamily(
            "pynixd_store_filesystem_available_bytes",
            "Space on that file system available to this user",
            value=stat.f_bavail * stat.f_frsize,
        )


REGISTRY.register(StoreSpaceCollector())


def get_metrics_response() -> tuple[bytes, str]:
    """Generate a Prometheus-formatted metrics response."""
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
