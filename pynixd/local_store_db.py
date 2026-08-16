"""Direct SQLite access to the local Nix store database.

Provides a connection pool for direct SQL queries against the local store DB.
Operations that support fast-path SQL queries use ``store.db.acquire_conn()``
directly rather than going through a dispatcher.

Registration time updates are batched writes using Lix's recursive CTE
that touches the full closure (references, derivers, deriver references)
of each seed path.

If the database can't be opened (permissions, missing file, wrong schema),
logs a warning and becomes unavailable — callers fall back to the daemon.

pynixd also keeps tables of its own in this file. `db_migrations` owns their
schema and their version, and `open` migrates them. The two answers are
separate: `active` says that Nix's tables are readable, and `schema.usable`
says that pynixd's tables are at the version this pynixd knows. A store can
give the first and refuse the second, and then every fast path that reads
`ValidPaths` still works.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import TYPE_CHECKING

import aiosqlite
import anyio
import structlog

from .db_migrations import (
    DERIVATION_STATS_TABLE,
    PATH_ACCESS_TABLE,
    SchemaState,
    apply_migrations,
)
from .store_path import StorePath

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterable

    from .serde.aliases import StorePathSet

log = structlog.get_logger(__name__)


# ── SQL constants ─────────────────────────────────────────────────────

# Lix's UpdateRegistrationTimeRecursive — walks the full closure of each seed
# path: its references, its deriver, and the references of that deriver. The
# parameter is a JSON array of store paths.
_CLOSURE_OF_SEEDS = """
    WITH RECURSIVE closure(id) AS (
        SELECT id FROM ValidPaths WHERE path IN (SELECT value FROM json_each(?))
        UNION
        SELECT r.reference
        FROM closure c JOIN Refs r ON c.id = r.referrer
        UNION
        SELECT deriver_vp.id
        FROM closure c
        JOIN ValidPaths current_vp ON c.id = current_vp.id
        JOIN ValidPaths deriver_vp ON current_vp.deriver = deriver_vp.path
        WHERE current_vp.deriver IS NOT NULL
        UNION
        SELECT r.reference
        FROM closure c
        JOIN ValidPaths current_vp ON c.id = current_vp.id
        JOIN ValidPaths deriver_vp ON current_vp.deriver = deriver_vp.path
        JOIN Refs r ON deriver_vp.id = r.referrer
        WHERE current_vp.deriver IS NOT NULL
    )
    SELECT id FROM closure
"""

UPDATE_REGTIME = f"""
UPDATE ValidPaths
SET registrationTime = unixepoch()
WHERE id IN ({_CLOSURE_OF_SEEDS})
"""

TOUCH_PATH_ACCESS = f"""
INSERT INTO {PATH_ACCESS_TABLE} (path, lastReferencedAt)
SELECT path, unixepoch() FROM ValidPaths WHERE id IN ({_CLOSURE_OF_SEEDS})
ON CONFLICT (path) DO UPDATE SET lastReferencedAt = excluded.lastReferencedAt
"""

QUERY_UNREFERENCED_SINCE = f"""
SELECT path FROM {PATH_ACCESS_TABLE} WHERE lastReferencedAt < ?
"""

# The join the tables of pynixd get by living in Nix's own database.
PRUNE_PATH_ACCESS = f"""
DELETE FROM {PATH_ACCESS_TABLE}
WHERE path NOT IN (SELECT path FROM ValidPaths)
"""

INSERT_BUILD_STATS = f"""
INSERT OR REPLACE INTO {DERIVATION_STATS_TABLE}
(pname, platform, derivation_json, cpu_user_us, cpu_system_us, duration_ms, last_built_at)
VALUES (?, ?, ?, ?, ?, ?, unixepoch())
"""

QUERY_BUILD_STATS_HINT = f"""
SELECT duration_ms FROM {DERIVATION_STATS_TABLE}
WHERE pname = ? AND platform = ?
ORDER BY last_built_at DESC
LIMIT 1
"""

QUERY_BUILD_STATS_CROSS_PLATFORM = f"""
SELECT AVG(duration_ms) FROM {DERIVATION_STATS_TABLE}
WHERE pname = ?
"""

_DEFAULT_REFERENCE_FLUSH_INTERVAL = 5.0


class LocalStoreDB:
    """Connection pool and dispatcher for Nix store SQLite.

    Operation types implement their own DB logic via ``execute_db(db)``.
    This class only manages connections and dispatches.

    Use the async factory ``await LocalStoreDB.open(store_path)`` to create.
    """

    def __init__(
        self,
        db_path: Path | None,
        store_path: Path | None,
        read_only: bool,
        reference_flush_interval: float,
        max_conns: int = 8,
    ) -> None:
        self.db_path = db_path
        self.store_path = store_path
        self.read_only: bool = read_only
        self.reference_flush_interval = reference_flush_interval

        self.pending_references: set[str] = set()
        """Full store paths that something referenced since the last flush.

        Plain strings, because two `StorePath` classes reach this set and both
        SQL statements want the text. `pynixd.store_path.StorePath` keeps the
        path without the `/nix/store/` prefix and adds it back in `__str__`,
        and the wire `StorePath` is a `str` of the whole path.
        """

        self.flush_task: asyncio.Task[None] | None = None

        self.schema = SchemaState(version=0, usable=False, reason="the schema was never checked")
        """The state of the tables pynixd owns. `open` replaces it.

        It starts unusable, so a `LocalStoreDB` built by hand answers no query
        that needs one of those tables. `active` alone cannot say: it reports
        that Nix's own tables are readable, which is a different question.
        """

        self._all_conns: list[aiosqlite.Connection] = []
        self._idle_conns: list[aiosqlite.Connection] = []
        self._pool_lock = anyio.Lock()
        self._sem = anyio.Semaphore(max_conns)

    @classmethod
    def inactive(
        cls,
        store_path: Path | None,
        *,
        reference_flush_interval: float = _DEFAULT_REFERENCE_FLUSH_INTERVAL,
    ) -> LocalStoreDB:
        """An instance that answers no query, for a store with no usable database.

        Three callers want this and each built it by hand: no database file,
        a database that would not open, and a store this class must not read
        at all (`LocalDBStore._refuses_a_database`).
        """
        return cls(
            db_path=None,
            store_path=store_path,
            read_only=True,
            reference_flush_interval=reference_flush_interval,
        )

    @property
    def active(self) -> bool:
        return self.db_path is not None

    @asynccontextmanager
    async def acquire_conn(self) -> AsyncIterator[aiosqlite.Connection]:
        """Acquire a connection from the pool."""
        if not self.active:
            raise RuntimeError("Database not active")

        await self._sem.acquire()
        conn: aiosqlite.Connection | None = None
        try:
            async with self._pool_lock:
                if self._idle_conns:
                    conn = self._idle_conns.pop()

            if conn is None:
                mode = "ro" if self.read_only else "rw"
                uri = f"file:{self.db_path}?mode={mode}"
                conn = await aiosqlite.connect(uri, uri=True)
                async with self._pool_lock:
                    self._all_conns.append(conn)

            yield conn
        finally:
            if conn is not None:
                async with self._pool_lock:
                    self._idle_conns.append(conn)
            self._sem.release()

    @asynccontextmanager
    async def execute(
        self,
        query: str,
        params: tuple = (),
    ) -> AsyncIterator[aiosqlite.Cursor]:
        """Execute a query and return the cursor.

        Convenience method that acquires a connection, runs the query,
        and returns the cursor. Usage::

            async with db.execute("SELECT * FROM Foo WHERE id = ?", (id,)) as cursor:
                row = await cursor.fetchone()

        For multiple queries or complex flows, use acquire_conn() directly.

        The cursor is closed when the block ends, and it has to be. A caller
        that reads one row of a query that could return more leaves the
        statement open, and an open statement holds a shared lock on the
        database file. The connection then goes back to the pool with that
        lock, and in a rollback journal every writer waits behind it. Almost
        every caller here calls `fetchone`, so almost every call left one.
        """
        async with self.acquire_conn() as conn:
            cursor = await conn.execute(query, params)
            try:
                yield cursor
            finally:
                with suppress(ValueError, aiosqlite.Error):
                    # ValueError: aiosqlite raises it for a connection that
                    # `close_db_pool` already closed, which `flush_references`
                    # does when a write fails.
                    await cursor.close()

    @classmethod
    async def open(
        cls,
        store_path: Path,
        reference_flush_interval: float = _DEFAULT_REFERENCE_FLUSH_INTERVAL,
    ) -> LocalStoreDB:
        """Open the Nix store database. Returns an instance (possibly with no DB).

        This never raises. A store whose database pynixd cannot read gets an
        inactive instance, every fast path of `LocalDBStore` declines, and
        `DaemonStore.execute` uses the wire. That is what lets `use_db` default
        to true.
        """
        db_path = resolve_db_path(store_path)
        if db_path is None:
            return cls.inactive(store_path, reference_flush_interval=reference_flush_interval)

        db_dir = db_path.parent
        can_write = os.access(db_dir, os.W_OK)
        read_only = not can_write

        try:
            instance = cls(
                db_path=db_path,
                store_path=store_path,
                read_only=read_only,
                reference_flush_interval=reference_flush_interval,
            )

            async with instance.acquire_conn() as db:
                if not read_only:
                    # The journal mode is Nix's to choose, and this used to set
                    # WAL unconditionally. Nix picks it from `use-sqlite-wal`
                    # (`settings.useSQLiteWAL ? "wal" : "truncate"`) and rewrites
                    # the mode on every `LocalStore` open when it differs, so
                    # forcing it here made the two flip the file back and forth.
                    # The setting defaults to true, so nothing changes on a
                    # normal machine; where it is false -- WSL1, or a store a
                    # person put on a network filesystem -- it is false for a
                    # reason and pynixd must not overrule it.
                    async with db.execute("PRAGMA main.journal_mode") as cursor:
                        row = await cursor.fetchone()
                    journal_mode = str(row[0]).lower() if row else "unknown"
                    if journal_mode != "wal":
                        log.info(
                            "nix_db_journal_mode_not_wal",
                            db_path=db_path,
                            journal_mode=journal_mode,
                            detail="readers and writers of this database serialise; Nix chose the mode",
                        )
                # `async with`, and the row is read. A cursor that stops on
                # its first row holds a shared lock on the file until it is
                # closed, and this connection then goes back to the pool and
                # holds that lock for the life of the process. In a rollback
                # journal that blocks every writer, so `apply_migrations`
                # waited out its whole busy timeout and then reported the
                # database as locked. WAL hid it: a reader there does not
                # block a writer, and a Nix store is WAL unless
                # `use-sqlite-wal` is off.
                async with db.execute("SELECT 1 FROM ValidPaths LIMIT 1") as cursor:
                    await cursor.fetchone()

        except (aiosqlite.Error, OSError) as e:
            log.warning(
                "nix_db_open_failed",
                db_path=db_path,
                error=e,
            )
            return cls.inactive(store_path, reference_flush_interval=reference_flush_interval)

        instance.schema = await apply_migrations(db_path, read_only=read_only)
        if not instance.schema.usable:
            log.warning(
                "pynixd_tables_unavailable",
                db_path=db_path,
                version=instance.schema.version,
                reason=instance.schema.reason,
                detail="the fast paths that read Nix's own tables are not affected",
            )
        log.info(
            "local_store_db_active",
            db_path=db_path,
            mode="read-write" if not read_only else "read-only",
            pynixd_schema_version=instance.schema.version,
            pynixd_tables=instance.schema.usable,
        )
        return instance

    # ── Internal utility queries ──────────────────────────────────────
    # These are not operation dispatches but internal helpers used by
    # non-operation code (GC, build planner, http cache).

    async def query_paths_not_referenced_since(self, max_age_seconds: int) -> StorePathSet | None:
        """The paths nothing has referenced for `max_age_seconds`, for an LRU collector.

        This replaces `query_stale_paths`, which asked the same question of
        `ValidPaths.registrationTime`. Nothing called it, and the column it
        read answers two questions at once. `PynixdPathAccess` answers one.

        A path with no row here has never been referenced through pynixd. It
        is not reported, because "never seen" and "seen long ago" are
        different, and only the second one is safe to collect on this
        evidence alone.
        """
        if not self.active or not self.schema.usable:
            return None
        try:
            cutoff = int(time.time()) - max_age_seconds
            async with self.execute(QUERY_UNREFERENCED_SINCE, (cutoff,)) as cursor:
                rows = await cursor.fetchall()
            return {StorePath(r[0]) for r in rows}
        except aiosqlite.Error:
            log.debug("query_paths_not_referenced_since_failed", exc_info=True)
            return None

    async def prune_path_access(self) -> int:
        """Drop the rows for paths the store no longer holds. Returns the count.

        This is the join that keeping pynixd's tables inside Nix's database
        buys: one statement compares `PynixdPathAccess` against `ValidPaths`.
        Without it the table grows for ever, because a path that the garbage
        collector deletes leaves its access time behind.
        """
        if not self.active or self.read_only or not self.schema.usable:
            return 0
        try:
            async with self.acquire_conn() as db:
                cursor = await db.execute(PRUNE_PATH_ACCESS)
                removed = cursor.rowcount
                await db.commit()
        except aiosqlite.Error:
            log.warning("prune_path_access_failed", exc_info=True)
            return 0
        if removed > 0:
            log.debug("path_access_pruned", removed=removed)
        return max(removed, 0)

    # ── Last reference times ──────────────────────────────────────────

    def mark_path(self, path: StorePath | str) -> None:
        """Note that something referenced `path` just now."""
        self.mark_paths((path,))

    def mark_paths(self, paths: Iterable[StorePath | str]) -> None:
        """Note that something referenced each of `paths` just now.

        The write happens later. `flush_loop` drains the set every few
        seconds, so a burst of queries over one closure costs one statement
        and not one for each path.
        """
        if self.active and not self.read_only:
            self.pending_references.update(str(path) for path in paths)

    async def record_build_stats(
        self,
        pname: str,
        platform: str,
        derivation_json: str,
        cpu_user_us: int | None,
        cpu_system_us: int | None,
        duration_ms: int,
    ) -> None:
        """Record build statistics for a derivation."""
        if not self.active or self.read_only or not self.schema.usable:
            return
        try:
            async with self.acquire_conn() as db:
                await db.execute(
                    INSERT_BUILD_STATS,
                    (
                        pname,
                        platform,
                        derivation_json,
                        cpu_user_us,
                        cpu_system_us,
                        duration_ms,
                    ),
                )
                await db.commit()
        except aiosqlite.Error:
            log.warning("record_build_stats_failed", pname=pname, exc_info=True)

    async def get_build_stats_hint(
        self,
        pname: str,
        platform: str,
    ) -> int | None:
        """Get an expected duration hint for a derivation (in ms).

        Matches on pname + platform, returning the most recent duration.
        Falls back to cross-platform average if no same-platform entry exists.
        """
        if not self.active or not self.schema.usable:
            return None
        try:
            # 1. Most recent same-platform duration
            async with self.execute(
                QUERY_BUILD_STATS_HINT,
                (pname, platform),
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return int(row[0])

            # 2. Fallback to platform-agnostic average for this pname
            async with self.execute(
                QUERY_BUILD_STATS_CROSS_PLATFORM,
                (pname,),
            ) as cursor:
                row = await cursor.fetchone()
                if row and row[0] is not None:
                    return int(row[0])
        except (aiosqlite.Error, ValueError):
            log.debug("get_build_stats_hint_failed", pname=pname, exc_info=True)
        return None

    async def flush_references(self) -> None:
        """Write the pending reference times, over the closure of each path.

        Two places record the same moment, and both are wanted.

        `ValidPaths.registrationTime` makes stock `nix-collect-garbage
        --delete-older-than` collect by last use rather than by age, because
        that command reads this column. pynixd needs no code for that, and
        losing it would remove a feature from a program that is not pynixd.

        `PynixdPathAccess` is the column that says what it means.
        `registrationTime` claims to be when the path entered the store, and
        `nix path-info --json` reports it as that, so one number cannot answer
        both questions afterwards. Issue #166 has the whole argument.
        """
        if not self.active or self.read_only:
            return
        if not self.pending_references:
            return

        paths = self.pending_references
        self.pending_references = set()

        try:
            t0 = time.monotonic()
            paths_json = json.dumps(sorted(paths))
            async with self.acquire_conn() as db:
                await db.execute(UPDATE_REGTIME, (paths_json,))
                if self.schema.usable:
                    await db.execute(TOUCH_PATH_ACCESS, (paths_json,))
                await db.commit()
            elapsed = time.monotonic() - t0
            log.debug(
                "db_flush_complete",
                seed_count=len(paths),
                path_access=self.schema.usable,
                elapsed_ms=elapsed * 1000,
            )
        except aiosqlite.Error:
            log.exception("db_flush_failed")
            await self.close_db_pool()

    def start(self) -> None:
        """Start background regtime flush task. Call from async context."""
        if not self.active or self.flush_task is not None or self.read_only:
            return
        self.flush_task = asyncio.create_task(self.flush_loop())

    async def flush_loop(self) -> None:
        try:
            while True:
                await anyio.sleep(self.reference_flush_interval)
                try:
                    await self.flush_references()
                except aiosqlite.Error:
                    log.exception("db_flush_loop_iteration_failed")
        except anyio.get_cancelled_exc_class():
            with suppress(Exception):
                await self.flush_references()
        except Exception:
            log.exception("db_flush_loop_crashed")

    # ── Lifecycle ─────────────────────────────────────────────────────

    async def close(self) -> None:
        """Stop flush task, flush pending writes, close database."""
        if self.flush_task is not None:
            self.flush_task.cancel()
            with suppress(BaseException):
                await self.flush_task
            self.flush_task = None
        await self.flush_references()
        await self.close_db_pool()

    async def close_db_pool(self) -> None:
        async with self._pool_lock:
            for db in self._all_conns:
                with suppress(Exception):
                    await db.close()
            self._all_conns.clear()
            self._idle_conns.clear()
            self.db_path = None


def resolve_db_path(store_path: Path) -> Path | None:
    """The `db.sqlite` of a store root, or `None` when there is none to use.

    Returns `None` rather than raising. `LocalStoreDB.open` is what decides
    whether the SQLite fast paths are available, and `use_db` defaults to
    true, so a store pynixd cannot read has to degrade and not stop the
    daemon.

    This used to `mkdir(parents=True)` the database directory whenever the
    file was missing, and let the `OSError` out. Two things went wrong with
    that. A store root pynixd may not write -- a read-only file system, or a
    store owned by another user -- raised straight out of `open` and took the
    daemon's startup with it. And a path that holds no Nix store at all got
    `nix/var/nix/db/` created inside it by a function named "resolve".

    The directory is still created, because a managed daemon writes its
    database there and `LocalDBStore.start` calls `ensure_daemon` first, so
    the usual case is a directory that already exists. A failure to create it
    now means the fast paths are off, and nothing more.
    """
    if store_path == Path("/") or not store_path:
        db_path = Path("/nix/var/nix/db/db.sqlite")
    else:
        db_path = store_path / "nix" / "var" / "nix" / "db" / "db.sqlite"

    if db_path.exists():
        return db_path

    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log.warning(
            "nix_db_directory_unavailable",
            db_path=str(db_path),
            error=str(exc),
            detail="the SQLite fast paths are off for this store; every query uses the wire",
        )
        return None
    return db_path
