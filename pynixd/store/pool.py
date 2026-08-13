"""
Connection pooling and concurrency limiting for Nix daemon connections.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import AbstractAsyncContextManager, asynccontextmanager, suppress
from contextvars import ContextVar
from typing import TYPE_CHECKING

import anyio
import structlog

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from ..connection import Connection
    from ..monitor import ResourceGate

log = structlog.get_logger(__name__)


# Per-store connection holder tracking via ContextVar
# Tracks nested connection count per store_id
_nested_conns: ContextVar[dict[str, int]] = ContextVar("_nested_conns")


class ConnectionPool:
    """Manages a pool of reusable daemon connections with concurrency limiting and idle TTL.

    Tracks connection nesting per-task via a ContextVar to detect re-entrant
    acquisitions and allocate fresh connections to avoid deadlock.
    """

    def __init__(
        self,
        store_id: str,
        factory: Callable[[], Awaitable[Connection]],
        gate: ResourceGate,
        idle_ttl: float = 10.0,
        max_connections: int = 64,
        on_connection_created: Callable[[Connection], None] | None = None,
    ) -> None:
        """Configure pool with connection factory, memory gate, and concurrency limits."""

        self.store_id = store_id
        self.factory = factory
        self.gate = gate
        self.idle_ttl = idle_ttl
        self._slots = anyio.Semaphore(max_connections)
        self.on_connection_created = on_connection_created

        self.active_connections = 0
        self.idle_conns: list[tuple[Connection, float]] = []
        self.all_conns: list[Connection] = []
        self.sweep_task: asyncio.Task[None] | None = None

    @property
    def in_flight(self) -> int:
        """Number of currently active connections."""
        return self.active_connections

    @property
    def stats(self) -> str:
        """Human-readable pool statistics."""
        return f"active={self.active_connections} idle={len(self.idle_conns)} total={len(self.all_conns)}"

    def start_sweep(self) -> None:
        """Start the idle connection sweep task if not already running."""

        """Start the idle sweep task if not already running."""
        if self.sweep_task is None or self.sweep_task.done():
            self.sweep_task = asyncio.create_task(self.sweep_idle())

    async def sweep_idle(self) -> None:
        """Periodically close idle connections that have expired."""
        while self.idle_conns:
            await anyio.sleep(self.idle_ttl / 2)
            now = time.monotonic()

            expired: list[tuple[Connection, float]] = []
            for item in self.idle_conns:
                conn, returned_at = item
                if now - returned_at >= self.idle_ttl:
                    expired.append(item)

            # Remove from tracking lists synchronously to prevent race conditions
            # with connections returned during the 'await conn.close()' yields below.
            for item in expired:
                if item in self.idle_conns:
                    self.idle_conns.remove(item)
                conn = item[0]
                if conn in self.all_conns:
                    self.all_conns.remove(conn)

            # Perform the async closures safely
            for conn, _ in expired:
                log.debug(
                    "pool_closing_expired_idle",
                    store_id=self.store_id,
                    conn_id=conn.id,
                )
                with suppress(Exception):
                    await conn.close()

    async def get_or_create_conn(self) -> Connection:
        """Return a reusable idle connection, or create a new one."""

        """Pop an idle connection or create a new one."""
        now = time.monotonic()
        while self.idle_conns:
            candidate, returned_at = self.idle_conns.pop()
            if now - returned_at >= self.idle_ttl:
                log.debug(
                    "pool_discarding_expired",
                    store_id=self.store_id,
                    conn_id=candidate.id,
                )
                if candidate in self.all_conns:
                    self.all_conns.remove(candidate)
                with suppress(Exception):
                    await candidate.close()
                continue

            if candidate.dirty or await candidate.r.is_dirty():
                log.warning(
                    "pool_discarding_dirty_conn",
                    store_id=self.store_id,
                    conn_id=candidate.id,
                    op_log=" -> ".join(candidate.op_log[-10:]) or "(empty)",
                )
                if candidate in self.all_conns:
                    self.all_conns.remove(candidate)
                with suppress(Exception):
                    await candidate.close()
                continue

            log.debug(
                "pool_reusing_conn",
                store_id=self.store_id,
                conn_id=candidate.id,
            )
            return candidate

        conn = await self.factory()
        self.all_conns.append(conn)
        if self.on_connection_created:
            self.on_connection_created(conn)

        log.debug(
            "pool_created_connection",
            store_id=self.store_id,
            conn_id=conn.id,
            pool_stats=self.stats,
        )
        return conn

    @asynccontextmanager
    async def acquire(
        self,
        kind: str | None = None,
    ) -> AsyncIterator[Connection]:
        """Acquire a connection from the shared pool.

        Wait for memory pressure to subside before allocating a new connection.
        If the same task that is already holding a connection tries to
        acquire again (re-entry), a new connection is allocated immediately
        to avoid deadlock.
        """
        # Use a copy to avoid mutating a shared default or parent context dict
        counts = _nested_conns.get({}).copy()
        in_use = counts.get(self.store_id, 0)
        re_entrant = in_use > 0

        if re_entrant:
            log.warning(
                "store_reentrant_acquire",
                store_id=self.store_id,
                kind=kind,
                nesting_level=in_use,
            )
            conn = await self.factory()
            self.all_conns.append(conn)

            # Increment nesting count in a NEW dict to ensure task isolation
            counts[self.store_id] = in_use + 1
            _nested_conns.set(counts)

            try:
                async with conn:
                    yield conn
            finally:
                # Decrement nesting count
                counts = _nested_conns.get({}).copy()
                counts[self.store_id] = counts.get(self.store_id, 0) - 1
                _nested_conns.set(counts)

                # Discard re-entrant connections immediately to avoid pool bloat
                if conn in self.all_conns:
                    self.all_conns.remove(conn)
                with suppress(Exception):
                    await conn.close()
            return

        await self._slots.acquire()
        try:
            # Wait for memory safety before allocating a connection.
            await self.gate.wait_mem_clear()

            self.active_connections += 1
            conn: Connection | None = None
            try:
                conn = await self.get_or_create_conn()

                # Increment nesting count
                counts = _nested_conns.get({}).copy()
                counts[self.store_id] = counts.get(self.store_id, 0) + 1
                _nested_conns.set(counts)

                try:
                    async with conn:
                        yield conn
                finally:
                    # Decrement nesting count
                    counts = _nested_conns.get({}).copy()
                    counts[self.store_id] = counts.get(self.store_id, 0) - 1
                    _nested_conns.set(counts)

                    if conn is not None:
                        if conn.dirty:
                            log.warning(
                                "store_discarding_dirty_connection",
                                store_id=self.store_id,
                                conn_id=conn.id,
                                op_log=" -> ".join(conn.op_log[-10:]) or "(empty)",
                            )
                            if conn in self.all_conns:
                                self.all_conns.remove(conn)
                            with suppress(Exception):
                                await conn.close()
                        else:
                            self.idle_conns.append((conn, time.monotonic()))
                            self.start_sweep()
            finally:
                self.active_connections -= 1
        finally:
            self._slots.release()

    def build_conn(self) -> AbstractAsyncContextManager[Connection]:
        """Acquire a connection for build operations."""
        return self.acquire("build")

    def transfer_conn(self) -> AbstractAsyncContextManager[Connection]:
        """Acquire a connection for transfer operations."""
        return self.acquire("transfer")

    async def close(self) -> None:
        """Close all connections and cancel the idle sweep task."""
        if self.sweep_task is not None:
            self.sweep_task.cancel()
            with suppress(BaseException):
                await self.sweep_task
            self.sweep_task = None

        for conn in self.all_conns:
            with suppress(Exception):
                await conn.close()
        self.all_conns.clear()
        self.idle_conns.clear()
