"""The schema of the tables that pynixd adds to a Nix store database.

pynixd keeps its own tables in Nix's `db.sqlite`. Nix reads its own tables by
name and never enumerates the file, so an extra table is invisible to it. The
gain is that a query can join pynixd's rows against `ValidPaths` in one
statement, with no second file and no attach.

**pynixd counts its schema separately from Nix.** Nix keeps its version in the
text file `nix/var/nix/db/schema`, beside the database. pynixd keeps its own
in the `PynixdSchema` table, and the two never meet. pynixd does not use
`PRAGMA user_version` either: that is one integer for the whole file, Nix does
not use it today, and two writers of one slot cannot both be right.

Every table that pynixd owns starts with `Pynixd`. The prefix is what makes
the tables of the two programs tellable apart inside one file, and it removes
the chance that Nix later adds a table of the same plain name.

Before this module, `LocalStoreDB.open` ran `DROP TABLE IF EXISTS
DerivationStats` and created the table again, on every start. That is a
migration that answers every version question with "delete the data". It also
made the build statistics useless for the thing that reads them: the
scheduler asks `get_build_stats_hint` how long a derivation took last time,
and the answer was always "no record" after a restart.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import aiosqlite
import structlog

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

log = structlog.get_logger(__name__)

TABLE_PREFIX = "Pynixd"
"""Each table pynixd owns starts with this. See the module docstring."""

SCHEMA_TABLE = f"{TABLE_PREFIX}Schema"

_CREATE_SCHEMA_TABLE = f"""
CREATE TABLE IF NOT EXISTS {SCHEMA_TABLE} (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    version INTEGER NOT NULL,
    updatedAt INTEGER NOT NULL
)
"""

_BUSY_TIMEOUT_MS = 15_000
"""How long to wait for the write lock of the store database.

Nix waits an hour (`sqlite.cc`, `sqlite3_busy_timeout`), because a Nix command
that cannot write has failed. pynixd waits far less, because it has an answer
that is not failure: run without the tables of this module, and keep every
other operation. A daemon that is slow to start is worse than a daemon that
starts without a statistics table.
"""


@dataclass(frozen=True)
class Migration:
    """One step from schema version `version - 1` to `version`.

    `creates` and `drops` name the tables that the step adds and removes.
    They are not documentation: `expected_tables` folds them to get the set of
    tables that a given version must have, and `apply_migrations` compares
    that set against the file. See `_repair_reason`.
    """

    version: int
    name: str
    statements: tuple[str, ...]
    creates: tuple[str, ...] = ()
    drops: tuple[str, ...] = ()


DERIVATION_STATS_TABLE = f"{TABLE_PREFIX}DerivationStats"

MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        version=1,
        name="derivation-stats",
        statements=(
            f"CREATE TABLE IF NOT EXISTS {DERIVATION_STATS_TABLE} ("
            "pname TEXT NOT NULL, "
            "platform TEXT NOT NULL, "
            "derivation_json TEXT, "
            "cpu_user_us INTEGER, "
            "cpu_system_us INTEGER, "
            "duration_ms INTEGER NOT NULL, "
            "last_built_at INTEGER NOT NULL, "
            "PRIMARY KEY (pname, platform)"
            ")",
            f"CREATE INDEX IF NOT EXISTS idx_pynixd_drv_stats_lookup ON {DERIVATION_STATS_TABLE}(pname, platform)",
            # The unprefixed table of the version that had no versions. Its
            # rows are not worth keeping: the code that made it dropped it on
            # every start, so it never held more than one run of builds.
            "DROP TABLE IF EXISTS DerivationStats",
        ),
        creates=(DERIVATION_STATS_TABLE,),
    ),
)

TARGET_VERSION = MIGRATIONS[-1].version if MIGRATIONS else 0


def expected_tables(version: int) -> frozenset[str]:
    """The tables that a database at `version` must hold."""
    tables: set[str] = {SCHEMA_TABLE}
    for migration in MIGRATIONS:
        if migration.version > version:
            break
        tables.update(migration.creates)
        tables.difference_update(migration.drops)
    return frozenset(tables)


@dataclass(frozen=True)
class SchemaState:
    """What `apply_migrations` found, and what the caller may do with it."""

    version: int
    usable: bool
    reason: str | None = None
    applied: tuple[str, ...] = field(default_factory=tuple)


def _unusable(version: int, reason: str) -> SchemaState:
    return SchemaState(version=version, usable=False, reason=reason)


async def _table_names(conn: aiosqlite.Connection) -> frozenset[str]:
    async with conn.execute(
        f"SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE '{TABLE_PREFIX}%'",
    ) as cursor:
        rows = await cursor.fetchall()
    return frozenset(str(row[0]) for row in rows)


async def _current_version(conn: aiosqlite.Connection, tables: frozenset[str]) -> int:
    """The recorded version, or 0 when this database has no pynixd tables yet."""
    if SCHEMA_TABLE not in tables:
        return 0
    async with conn.execute(f"SELECT version FROM {SCHEMA_TABLE} WHERE id = 1") as cursor:
        row = await cursor.fetchone()
    return int(row[0]) if row is not None else 0


def _repair_reason(version: int, tables: frozenset[str]) -> str | None:
    """Why the recorded version does not describe the file, or `None`.

    A version of pynixd that predates this module drops and recreates its
    table on every start. Run that version against a database a newer pynixd
    migrated, and the file loses a table while the version column still claims
    to have it. The newer pynixd then runs no migration, because the number
    says it is current, and queries a table that is not there.

    Every statement of every migration is written to run twice, so the answer
    is to migrate again from zero.
    """
    missing = expected_tables(version) - tables
    if not missing:
        return None
    return f"the database records schema version {version}, but {', '.join(sorted(missing))} is missing"


async def apply_migrations(db_path: Path, *, read_only: bool) -> SchemaState:
    """Bring the pynixd tables of `db_path` up to `TARGET_VERSION`.

    This never raises. Every failure gives a `SchemaState` with `usable`
    false, and the caller answers each query that needs a pynixd table with
    "no record". The tables of Nix are not touched and stay readable, so the
    fast paths that read `ValidPaths` keep working either way.

    The whole migration runs in one `BEGIN IMMEDIATE` transaction, and reads
    the recorded version inside it. Two pynixd daemons that start together on
    one store therefore cannot both migrate: the second one takes the write
    lock after the first commits, and reads the version the first one wrote.
    """
    if TARGET_VERSION == 0:
        return SchemaState(version=0, usable=True)

    mode = "ro" if read_only else "rw"
    try:
        # A connection of its own, and not one from the pool. The migration
        # drives its transaction by hand, so it must own the connection's
        # isolation level, and a pooled connection is shared with code that
        # calls `commit` for itself.
        async with aiosqlite.connect(
            f"file:{db_path}?mode={mode}",
            uri=True,
            isolation_level=None,
        ) as conn:
            await conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
            if read_only:
                return await _inspect_read_only(conn)
            return await _migrate(conn)
    except (aiosqlite.Error, OSError) as exc:
        log.warning("pynixd_schema_unavailable", db_path=str(db_path), error=str(exc))
        return _unusable(0, f"the pynixd tables could not be opened: {exc}")


async def _inspect_read_only(conn: aiosqlite.Connection) -> SchemaState:
    """Report whether tables pynixd cannot write are already current."""
    tables = await _table_names(conn)
    version = await _current_version(conn, tables)
    if version != TARGET_VERSION:
        return _unusable(
            version,
            f"the store database is read-only at schema version {version}, and pynixd needs version {TARGET_VERSION}",
        )
    repair = _repair_reason(version, tables)
    if repair is not None:
        return _unusable(version, f"{repair}, and the store database is read-only")
    return SchemaState(version=version, usable=True)


async def _migrate(conn: aiosqlite.Connection) -> SchemaState:
    await conn.execute("BEGIN IMMEDIATE")
    try:
        tables = await _table_names(conn)
        version = await _current_version(conn, tables)

        if version > TARGET_VERSION:
            await conn.execute("ROLLBACK")
            return _unusable(
                version,
                f"the store database holds pynixd schema version {version}, and this "
                f"pynixd knows version {TARGET_VERSION}. A newer pynixd wrote it.",
            )

        repair = _repair_reason(version, tables)
        if repair is not None:
            log.warning("pynixd_schema_repair", version=version, reason=repair)
            version = 0

        await conn.execute(_CREATE_SCHEMA_TABLE)
        applied = await _run_steps(conn, version)
        await conn.execute(
            f"INSERT INTO {SCHEMA_TABLE} (id, version, updatedAt) VALUES (1, ?, unixepoch()) "
            f"ON CONFLICT (id) DO UPDATE SET version = excluded.version, updatedAt = excluded.updatedAt",
            (TARGET_VERSION,),
        )
        await conn.execute("COMMIT")
    except aiosqlite.Error as exc:
        # One transaction holds every step, so the file still describes the
        # version it described before this call.
        try:
            await conn.execute("ROLLBACK")
        except aiosqlite.Error:
            log.debug("pynixd_schema_rollback_failed", exc_info=True)
        log.warning("pynixd_schema_migration_failed", error=str(exc))
        return _unusable(0, f"a pynixd schema migration failed: {exc}")

    if applied:
        log.info("pynixd_schema_migrated", version=TARGET_VERSION, applied=list(applied))
    return SchemaState(version=TARGET_VERSION, usable=True, applied=applied)


async def _run_steps(conn: aiosqlite.Connection, from_version: int) -> tuple[str, ...]:
    applied: list[str] = []
    for migration in MIGRATIONS:
        if migration.version <= from_version:
            continue
        for statement in migration.statements:
            await conn.execute(statement)
        applied.append(migration.name)
    return tuple(applied)


def migration_versions() -> Sequence[int]:
    """The version of each migration, in order. `tests/unit` checks the shape."""
    return [migration.version for migration in MIGRATIONS]
