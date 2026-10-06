"""Failed checkouts return clean.

On 2026-10-06 every pynixd write to the store database failed for
four hours with `database is locked` while fresh connections wrote
fine: pooled connections carry their state across checkouts, and a
checkout that failed mid-write held the file lock until restart.
`acquire_conn` now rolls back on checkin; this pins that.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from pynixd.local_store_db import LocalStoreDB

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.anyio
async def test_failed_checkout_returns_clean(tmp_path: Path) -> None:
    """A write without commit does not survive its checkout."""
    db_path = tmp_path / "db.sqlite"
    # The pool opens `mode=rw`, which never creates: the file must exist.
    db_path.touch()
    db = LocalStoreDB(
        db_path=db_path,
        store_path=tmp_path,
        read_only=False,
        reference_flush_interval=60.0,
    )
    try:
        async with db.acquire_conn() as conn:
            await conn.execute("CREATE TABLE t (x INTEGER)")
            await conn.commit()
            await conn.execute("INSERT INTO t VALUES (1)")
            # No commit: a failure between write and commit. (The CREATE
            # commits on its own — sqlite autocommits DDL outside a
            # transaction — so the assertion below reads rows, not tables.)
        async with db.acquire_conn() as second:
            cursor = await second.execute("SELECT COUNT(*) FROM t")
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == 0
            await second.execute("INSERT INTO t VALUES (2)")
            await second.commit()
    finally:
        await db.close_db_pool()
