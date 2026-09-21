"""Prometheus metrics for pynixd, served from the `/metrics` route.

**Every label here is bounded.** A store path, a client address or a
derivation name would give one series per value, and a cache that serves a
cluster sees millions of them. `op` comes from `WIRE_REGISTRY`, `store_id`
from the configuration, and `result`, `route` and `transport` are written out
in this file.

`prometheus_client` registers `ProcessCollector`, `PlatformCollector` and
`GCCollector` on the default registry by itself, so `process_cpu_seconds_total`,
`process_resident_memory_bytes`, `process_open_fds` and `python_gc_*` are
already served. Do not add a gauge for any of them.
"""

from __future__ import annotations

import json
import os
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import TYPE_CHECKING
from weakref import WeakSet

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

if TYPE_CHECKING:
    from .store.pool import ConnectionPool

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

# --- Event loop metrics ---

# How long the loop goes between running ready callbacks. Everything pynixd
# serves is answered by the loop, so this is the one number that says whether
# it can answer at all -- a transfer that does not yield stalls the loop, and a
# stalled loop accepts no connection and runs no probe handler.
#
# A TCP probe cannot see this. `connect()` is completed by the kernel from the
# listen backlog whether or not the application ever accepts, so it passes
# against a wedged process and fails only on a timeout. That is nixkube#53.
EVENT_LOOP_LAG = Gauge(
    "pynixd_event_loop_lag_seconds",
    "Seconds the event loop went without running a ready callback, over the last window",
)

EVENT_LOOP_LAG_MAX = Gauge(
    "pynixd_event_loop_lag_max_seconds",
    "Largest event loop stall seen since the process started",
)

# --- Daemon protocol ---
#
# `DaemonProxy.op_loop` already times every operation and prints the total when
# the session ends. A number that exists only in a closing log line answers
# nothing while the transfer is running, which is when somebody looks.

DAEMON_OPS = Counter(
    "pynixd_daemon_ops_total",
    "Daemon protocol operations this process has finished",
    ["op", "result"],  # result: ok, error
)

# The top bucket is 600 s on purpose. A `BuildPaths` waits for the build, so
# this histogram carries a request that runs for minutes beside one that
# answers from SQLite in microseconds.
DAEMON_OP_DURATION = Histogram(
    "pynixd_daemon_op_duration_seconds",
    "Time one daemon protocol operation took, from reading its number to its response",
    ["op"],
    buckets=(0.001, 0.005, 0.025, 0.1, 0.5, 2.5, 10, 60, 300, 600),
)

DAEMON_SESSIONS = Gauge(
    "pynixd_daemon_sessions",
    "Client sessions being served now",
    ["transport"],  # ssh, unix, reverse
)

# Not `pynixd_daemon_sessions_total`: `prometheus_client` strips the `_total`
# to get a counter's base name, and that base would collide with the gauge
# above. The registry rejects the collision at import, so this is a build
# failure and not a silent one.
DAEMON_SESSIONS_TOTAL = Counter(
    "pynixd_daemon_sessions_accepted_total",
    "Client sessions this process has accepted",
    ["transport"],
)

# --- NAR forwarding ---
#
# The server side of `AddMultipleToStore`: what a `nix copy` into pynixd
# spends its time and its memory on. This is the path that peaked at 0.979 of
# the payload in RAM and 3.074 s of CPU per 128 MiB before the backpressure and
# the SSH read-ahead landed, so a dashboard needs to see it directly.

NAR_FORWARD_BYTES = Counter(
    "pynixd_nar_forward_bytes_total",
    "NAR bytes forwarded from a client to the local daemon",
)

NAR_FORWARD_PATHS = Counter(
    "pynixd_nar_forward_paths_total",
    "Store paths forwarded from a client to the local daemon",
)

NAR_FORWARD_DURATION = Histogram(
    "pynixd_nar_forward_duration_seconds",
    "Time one AddMultipleToStore payload took to forward",
    buckets=(0.1, 0.5, 1, 5, 15, 60, 300, 600, 1800),
)

# A transfer that finished but whose source never reached EOF. The client is
# still holding the connection, and the handler gave up waiting after 10 s.
NAR_FORWARD_EOF_TIMEOUTS = Counter(
    "pynixd_nar_forward_source_eof_timeouts_total",
    "AddMultipleToStore payloads whose source did not reach EOF in time",
)

# --- Connection pools ---

POOL_CONNECTIONS_CREATED = Counter(
    "pynixd_store_pool_connections_created_total",
    "Daemon connections a store pool has opened",
    ["store_id"],
)

POOL_IDLE_EXPIRED = Counter(
    "pynixd_store_pool_idle_expired_total",
    "Pooled connections closed because they went idle or reached their lifetime",
    ["store_id"],
)

POOL_EMPTIED = Counter(
    "pynixd_store_pool_emptied_total",
    "Times a store pool dropped to no connection at all",
    ["store_id"],
)

# --- Substitution ---
#
# What a build waits on before it decides to build anything. A substituter
# that answers slowly and a substituter that answers "no" cost a client the
# same wall clock and are a different fault, and nothing here said which.

SUBSTITUTER_QUERIES = Counter(
    "pynixd_substituter_queries_total",
    "QueryPathInfo calls made to a substituter while choosing one",
    ["store_id", "result"],  # hit, miss, timeout, error, unsupported
)

SUBSTITUTER_QUERY_DURATION = Histogram(
    "pynixd_substituter_query_duration_seconds",
    "Time one substituter took to answer whether it has a path",
    ["store_id"],
    buckets=(0.01, 0.05, 0.25, 1, 5, 15, 60),
)

SUBSTITUTIONS = Counter(
    "pynixd_substitutions_total",
    "Paths pynixd tried to fetch from a substituter",
    ["result"],  # ok, error, no_candidate
)

SUBSTITUTION_DURATION = Histogram(
    "pynixd_substitution_duration_seconds",
    "Time spent importing one substituted path, the query for a candidate included",
    buckets=(0.05, 0.25, 1, 5, 15, 60, 300, 900),
)

SUBSTITUTED_BYTES = Counter(
    "pynixd_substituted_bytes_total",
    "NAR bytes imported from substituters",
)

# The queue's own health record, and not `STORE_HEALTHY`. That one is about a
# build store the scheduler dispatches to; this is about a substituter the
# queue decides whether to wait for, and a store can be both.
SUBSTITUTER_WAITED_FOR = Gauge(
    "pynixd_substituter_waited_for",
    "Whether the queue still blocks a selection on this substituter (1 = yes)",
    ["store_id"],
)

# --- HTTP binary cache clients ---

BINARY_CACHE_NARINFO = Counter(
    "pynixd_binary_cache_narinfo_total",
    "`.narinfo` requests pynixd made to an upstream HTTP binary cache",
    ["store_id", "result"],  # hit, miss, error
)

# --- Garbage collection ---
#
# Same shape as nixkube's GC series, so one dashboard reads both.

GC_CYCLES = Counter(
    "pynixd_gc_cycles_total",
    "Garbage collection passes this process has finished",
    ["result"],  # ok, error
)

GC_CYCLE_DURATION = Histogram(
    "pynixd_gc_cycle_duration_seconds",
    "Time one garbage collection pass took, planning included",
    buckets=(1, 5, 15, 30, 60, 120, 300, 600),
)

GC_PATHS_DELETED = Counter(
    "pynixd_gc_paths_deleted_total",
    "Store paths the collector has deleted",
)

GC_BYTES_FREED = Counter(
    "pynixd_gc_bytes_freed_total",
    "Bytes the collector reports it freed",
)

# Zero until the first pass finishes, so an alert reads it as
# `== 0 or time() - it > N`. A timestamp that stops advancing is what says the
# collector is stuck; no size gauge says that on its own.
GC_LAST_SUCCESS = Gauge(
    "pynixd_gc_last_success_timestamp_seconds",
    "When a garbage collection pass last finished, in unix seconds",
)

# --- HTTP binary cache ---

# `route` is the pattern aiohttp matched, not the path the client asked for.
# `/{hash}.narinfo` is one series; the path itself would be one series per
# store path.
HTTP_REQUESTS = Counter(
    "pynixd_http_requests_total",
    "Requests served on the HTTP interface",
    ["route", "method", "status"],
)

HTTP_REQUEST_DURATION = Histogram(
    "pynixd_http_request_duration_seconds",
    "Time one HTTP request took, including streaming its body",
    ["route", "method"],
    buckets=(0.001, 0.01, 0.1, 0.5, 2.5, 10, 60, 300),
)

HTTP_NAR_BYTES_SENT = Counter(
    "pynixd_http_nar_bytes_sent_total",
    "NAR bytes written to binary cache clients",
)

HTTP_NAR_BYTES_RECEIVED = Counter(
    "pynixd_http_nar_bytes_received_total",
    "NAR bytes accepted from binary cache uploads",
)

# --- Build info ---

BUILD_INFO = Gauge(
    "pynixd_build_info",
    "Always 1. The version rides on the label, which is how a dashboard joins on it",
    ["version"],
)

try:
    BUILD_INFO.labels(version=version("pynixd")).set(1)
except PackageNotFoundError:
    # Running from a source tree with no installed distribution. The series is
    # absent, which is a truthful answer, and not a reason to fail an import.
    log.debug("build_info_version_unavailable")


class StorePoolCollector:
    """Pool depth per store, read when a scrape asks for it.

    A collector and not three gauges, because the pool changes these counts on
    every acquire and release. A gauge kept in step costs an update per
    operation for a number nobody reads between scrapes, and it goes wrong the
    first time a path returns a connection by another route.

    The set holds weak references. A pool belongs to its store, and a store
    that goes away must not be held alive by the metrics registry.
    """

    def __init__(self) -> None:
        self._pools: WeakSet[ConnectionPool] = WeakSet()

    def register(self, pool: ConnectionPool) -> None:
        self._pools.add(pool)

    def collect(self):
        """Yield in-flight, idle and total connections for every live pool."""
        in_flight = GaugeMetricFamily(
            "pynixd_store_pool_in_flight_connections",
            "Pooled connections handed out and not yet returned",
            labels=["store_id"],
        )
        idle = GaugeMetricFamily(
            "pynixd_store_pool_idle_connections",
            "Pooled connections open and available",
            labels=["store_id"],
        )
        total = GaugeMetricFamily(
            "pynixd_store_pool_connections",
            "Connections the pool holds, in flight and idle",
            labels=["store_id"],
        )
        # Summed per `store_id`, and not one sample per pool. A store that is
        # removed and added again leaves the old pool in this set until the
        # collector runs, and two samples carrying the same labels make
        # Prometheus reject the **whole scrape** -- every series of this
        # process goes dark, not only these three.
        counts: dict[str, tuple[int, int, int]] = {}
        for pool in self._pools:
            was = counts.get(pool.store_id, (0, 0, 0))
            counts[pool.store_id] = (
                was[0] + pool.active_connections,
                was[1] + len(pool.idle_conns),
                was[2] + len(pool.all_conns),
            )
        for store_id, (busy, free, held) in counts.items():
            in_flight.add_metric([store_id], busy)
            idle.add_metric([store_id], free)
            total.add_metric([store_id], held)
        yield in_flight
        yield idle
        yield total


STORE_POOLS = StorePoolCollector()
REGISTRY.register(STORE_POOLS)


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


# --- appstarter ---

APPSTARTER_STATE = Path("var/appstarter/state.json")
"""Where `appstarter init` records what it put in the store, relative to the
store root. See `AppstarterCollector`."""


class AppstarterCollector:
    """Whether this pod runs what its deployment asks for, or the image's copy.

    `appstarter init` fetches the environment the deployment names, and when
    that fetch fails it seeds the store from the copy baked into the image
    instead. That is deliberate -- a pod that starts behind beats a pod that
    does not start -- and it is invisible from outside: the pod is Running and
    every probe passes either way. The two store paths in `state.json` are the
    only thing that says which happened.

    **Read from the file and on the scrape, not from the environment at
    import.** `appstarter run` puts both paths in the environment of the
    container it execs, and only some of these containers are started that way
    -- the builder runs its program directly. The store they share answers for
    all of them.

    **Absent rather than zero when the file cannot be read.** A process that
    cannot tell must not report "not degraded", which is the one answer that
    would hide exactly what this exists to show. Alert on `== 1`, and on
    `absent()` separately if the silence itself matters.

    **1 is a state, not an event: it cannot clear while the pod lives.**
    `appstarter init` is an `initContainer`, and a container restart does not
    re-run one -- a pod killed by its liveness probe comes back onto the same
    store and reports the same bit. Only a new pod, or a sandbox the kubelet
    recreates after a node restart, decides it again. So an alert on `== 1`
    needs no `for:` beyond a scrape or two, will not flap, and stays firing
    until somebody replaces the pod.

    The paths are deliberately not labels: this module's rule against store
    paths holds, the bit is what an alert needs, and the paths are in the
    pod's log and in `APPSTARTER_RUNNING_STORE_PATH`.

    The store root is the parent of the *real* store directory, so a chroot
    store finds its own state file rather than the host's.
    """

    def collect(self):
        """Yield the one bit, or nothing when no state was recorded."""
        path = Path(real_store_dir()).parent / APPSTARTER_STATE
        try:
            recorded = json.loads(path.read_text())
            wanted, running = recorded["wanted"], recorded["running"]
        except (OSError, ValueError, KeyError, TypeError):
            # Deliberately silent. `REGISTRY.register` calls `collect()` once
            # to check for a duplicate metric name, so anything written here
            # lands on stdout during import -- which broke the JSON a probe in
            # this suite reads out of a bare interpreter. A scrape every 15
            # seconds would then repeat it for ever. The absent series is the
            # signal.
            return
        yield GaugeMetricFamily(
            "pynixd_appstarter_degraded",
            "1 when this pod runs the environment baked into its image instead of the one the deployment asks for",
            value=float(wanted != running),
        )


REGISTRY.register(AppstarterCollector())


def get_metrics_response() -> tuple[bytes, str]:
    """Generate a Prometheus-formatted metrics response."""
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
