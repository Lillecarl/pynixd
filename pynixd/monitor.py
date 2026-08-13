"""
System resource monitoring and concurrency gating for pynixd stores.

All monitoring uses passive polling — no PSI triggers or eventfd.
Local stores poll /proc/pressure and cgroup files directly.
Remote (SSH) stores poll through SFTP-backed reads.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING

import anyio
import structlog

from .exceptions import ResourceExhaustedError
from .psi import (
    CgroupCpuStat,
    CpuUtil,
    MemInfo,
    SystemHealth,
    compute_cpu_util,
    count_cpus_from_proc_stat,
    parse_cpu_max,
    parse_cpu_stat,
    parse_meminfo,
    parse_psi_output,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from .config import PynixdSettings

log = structlog.get_logger(__name__)


class ResourceGate:
    """Async synchronization primitive that gates access based on system pressure."""

    def __init__(self) -> None:
        self.cpu_clear = asyncio.Event()
        self.mem_clear = asyncio.Event()
        self.io_clear = asyncio.Event()
        self.cpu_clear.set()
        self.mem_clear.set()
        self.io_clear.set()

    async def wait_mem_clear(self, timeout: float = 5.0) -> None:  # noqa: ASYNC109
        """Wait for Memory pressure to drop below threshold."""
        try:
            with anyio.fail_after(timeout):
                await self.mem_clear.wait()
        except TimeoutError:
            raise ResourceExhaustedError(
                "Memory pressure remains too high after timeout",
            ) from None


class ResourceMonitor(ABC):
    """Abstract base for system monitoring."""

    def __init__(self, gate: ResourceGate, settings: PynixdSettings) -> None:
        """Initialize a resource monitor.

        Args:
            gate: Shared gate used to block builds under pressure.
            settings: Application settings with pressure thresholds.
        """
        self.gate = gate
        self.settings = settings
        self.running = False
        self.task: asyncio.Task[None] | None = None
        self.health = SystemHealth()

    @abstractmethod
    async def run(self) -> None:
        """Monitor loop."""
        ...

    def start(self) -> None:
        """Start the monitor loop as a background task."""
        if not self.running:
            self.running = True
            self.task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        """Stop the monitor loop and cancel its background task."""
        self.running = False
        if self.task:
            self.task.cancel()
            with contextlib.suppress(Exception, anyio.get_cancelled_exc_class()):
                await self.task


class DummyResourceMonitor(ResourceMonitor):
    """Monitor for stores that don't support telemetry. Reports static healthy stats."""

    def __init__(
        self,
        gate: ResourceGate,
        settings: PynixdSettings,
        cpu_util: float = 0.0,
    ) -> None:
        """Initialize a dummy monitor.

        Args:
            gate: Shared gate (always set to healthy).
            settings: Application settings.
            cpu_util: Static CPU utilisation to report.
        """
        super().__init__(gate, settings)
        self.cpu_util = cpu_util

    async def run(self) -> None:
        """Report static healthy stats once, then idle until stopped."""
        log.info("dummy_resource_monitor_started", cpu_util=self.cpu_util)
        self.health = SystemHealth(
            cpu_util=CpuUtil(utilization=self.cpu_util, cores=1.0, throttled_pct=0.0),
            timestamp=time.monotonic(),
        )
        self.gate.cpu_clear.set()
        self.gate.mem_clear.set()
        self.gate.io_clear.set()
        while self.running:  # noqa: ASYNC110 — 60s PSI polling interval
            await anyio.sleep(60)


class GenericResourcePoller(ResourceMonitor):
    """Polling monitor that works over any read/exists functions (Local/SSH)."""

    def __init__(
        self,
        gate: ResourceGate,
        settings: PynixdSettings,
        read_fn: Callable[[str], Coroutine[None, None, str]],
        exists_fn: Callable[[str], Coroutine[None, None, bool]],
    ) -> None:
        """Initialize a generic polling monitor.

        Args:
            gate: Shared gate to toggle under pressure.
            settings: Application settings with pressure thresholds.
            read_fn: Async callable to read a file at a given path.
            exists_fn: Async callable to check whether a path exists.
        """
        super().__init__(gate, settings)
        self.read_fn = read_fn
        self.exists_fn = exists_fn
        self.interval = 5.0
        self.cpu_stat_prev: CgroupCpuStat | None = None
        self.cpu_cores: float | None = None

    async def run(self) -> None:
        """Periodically poll PSI, memory, and CPU stats and update the resource gate."""
        log.info("resource_poller_started")

        # 1. Detect CPU cores once
        try:
            if await self.exists_fn("/sys/fs/cgroup/cpu.max"):
                text = await self.read_fn("/sys/fs/cgroup/cpu.max")
                self.cpu_cores = parse_cpu_max(text)

            if self.cpu_cores is None and await self.exists_fn("/proc/stat"):
                text = await self.read_fn("/proc/stat")
                self.cpu_cores = float(count_cpus_from_proc_stat(text))
        except (PermissionError, FileNotFoundError, OSError):
            log.info("resource_poller_metadata_unavailable", info="cpu_cores")
            self.cpu_cores = 1.0
        except Exception:
            log.debug("cpu_core_detection_failed")
            self.cpu_cores = 1.0

        while self.running:
            try:
                # 2. Read PSI if available
                psi_text = ""
                has_psi = False
                try:
                    parts = []
                    for p in ["cpu", "memory", "io"]:
                        path = f"/proc/pressure/{p}"
                        if await self.exists_fn(path):
                            parts.append(await self.read_fn(path))
                            has_psi = True
                    if has_psi:
                        psi_text = "\n".join(parts)
                except (PermissionError, FileNotFoundError):
                    # PSI not available on this system
                    log.info("resource_poller_psi_unavailable")
                    has_psi = False
                except (OSError, ValueError, IndexError, AttributeError):
                    log.exception("resource_poller_psi_error")
                    has_psi = False

                # 3. Read Memory
                meminfo = None
                try:
                    # Prefer cgroupv2 memory.current/max if available (more accurate for containers)
                    if await self.exists_fn("/sys/fs/cgroup/memory.current"):
                        curr_text = await self.read_fn("/sys/fs/cgroup/memory.current")
                        curr = int(curr_text.strip())
                        max_text = await self.read_fn("/sys/fs/cgroup/memory.max")
                        max_raw = max_text.strip()
                        total = None if max_raw == "max" else int(max_raw)

                        # Fallback to /proc/meminfo for totals if cgroup is unlimited
                        if total is None and await self.exists_fn("/proc/meminfo"):
                            p_mem_text = await self.read_fn("/proc/meminfo")
                            p_mem = parse_meminfo(p_mem_text)
                            total = p_mem.mem_total * 1024  # parse_meminfo returns kB

                        meminfo = MemInfo(
                            mem_total=(total // 1024) if total else 0,
                            mem_available=((total - curr) // 1024) if total else 0,
                        )
                    elif await self.exists_fn("/proc/meminfo"):
                        text = await self.read_fn("/proc/meminfo")
                        meminfo = parse_meminfo(text)
                except (PermissionError, FileNotFoundError):
                    log.info("resource_poller_memory_unavailable")
                except (OSError, ValueError, IndexError):
                    log.exception("resource_poller_memory_error")

                # 4. Read CPU util
                cpu_util = None
                try:
                    if await self.exists_fn("/sys/fs/cgroup/cpu.stat"):
                        stat_text = await self.read_fn("/sys/fs/cgroup/cpu.stat")
                        stat = parse_cpu_stat(stat_text)
                        if self.cpu_stat_prev:
                            cpu_util = compute_cpu_util(
                                self.cpu_stat_prev,
                                stat,
                                self.cpu_cores,
                            )
                        self.cpu_stat_prev = stat
                except (PermissionError, FileNotFoundError):
                    log.info("resource_poller_cpu_unavailable")
                except (OSError, ValueError, IndexError):
                    log.exception("resource_poller_cpu_error")

                # 5. Update Health and Gate
                self.health = SystemHealth(
                    psi=parse_psi_output(psi_text) if has_psi else None,
                    meminfo=meminfo,
                    cpu_util=cpu_util,
                    timestamp=time.monotonic(),
                )

                # Reset gate if data is missing (assume healthy)
                if self.health.is_cpu_stressed(
                    self.settings.psi_cpu_threshold,
                    self.settings.max_cpu_util,
                ):
                    self.gate.cpu_clear.clear()
                else:
                    self.gate.cpu_clear.set()

                if self.health.is_mem_stressed(
                    self.settings.psi_mem_threshold,
                    self.settings.min_available_memory_mb * 1024,
                ):
                    self.gate.mem_clear.clear()
                else:
                    self.gate.mem_clear.set()

                if self.health.is_io_stressed(self.settings.psi_io_threshold):
                    self.gate.io_clear.clear()
                else:
                    self.gate.io_clear.set()

            except anyio.get_cancelled_exc_class():
                break
            except Exception:
                log.exception("resource_poller_tick_failed")

            await anyio.sleep(self.interval)


def create_monitor(gate: ResourceGate, settings: PynixdSettings) -> ResourceMonitor:
    """Factory to create a local passive-reading monitor."""

    async def local_read(path: str) -> str:
        with Path(path).open() as f:  # noqa: ASYNC230 — /proc reads are instant
            return f.read()

    async def local_exists(path: str) -> bool:
        return Path(path).exists()  # noqa: ASYNC240 — instant local FS check

    return GenericResourcePoller(gate, settings, local_read, local_exists)
