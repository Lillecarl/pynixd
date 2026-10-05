"""The scheduler records the closure it decides to build. Issue #65."""

from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import anyio
import pytest

from pynixd.db_migrations import BUILD_ACCESS_TABLE
from pynixd.local_store_db import LocalStoreDB
from pynixd.scheduler import Scheduler
from pynixd.store_layout import StoreLayout

if TYPE_CHECKING:
    from pathlib import Path

HELLO = "/nix/store/00000000000000000000000000000001-hello"
LIBC = "/nix/store/00000000000000000000000000000002-libc"


def _store_with_a_closure(tmp_path: Path) -> None:
    """A store database where `hello` references `libc`."""
    db_path = tmp_path / "nix" / "var" / "nix" / "db" / "db.sqlite"
    db_path.parent.mkdir(parents=True)
    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.execute(
            "CREATE TABLE ValidPaths ("
            "id INTEGER PRIMARY KEY, path TEXT UNIQUE NOT NULL, "
            "deriver TEXT, registrationTime INTEGER)",
        )
        conn.execute("CREATE TABLE Refs (referrer INTEGER, reference INTEGER)")
        conn.execute("INSERT INTO ValidPaths (id, path, registrationTime) VALUES (1, ?, 0)", (HELLO,))
        conn.execute("INSERT INTO ValidPaths (id, path, registrationTime) VALUES (2, ?, 0)", (LIBC,))
        conn.execute("INSERT INTO Refs (referrer, reference) VALUES (1, 2)")


async def _build_times(db: LocalStoreDB) -> set[str]:
    async with db.execute(f"SELECT path FROM {BUILD_ACCESS_TABLE}") as cursor:
        return {str(row[0]) for row in await cursor.fetchall()}


@pytest.mark.anyio
async def test_assignment_records_the_closure_without_waiting(tmp_path: Path) -> None:
    """Deciding to build `hello` records `hello` and `libc` as build-kind.

    The recording runs as a tracked task instead of slowing assignment:
    the test returns after the rows land, and the tracker holds no task
    once its recording finishes.
    """
    _store_with_a_closure(tmp_path)
    async with await LocalStoreDB.open(StoreLayout.chroot(tmp_path)) as db:
        scheduler = Scheduler.__new__(Scheduler)
        scheduler.local_store = cast(Any, SimpleNamespace(db=db))
        scheduler._mark_tasks = set()

        scheduler._record_build_closure(HELLO)

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            await db.flush_references()
            if await _build_times(db) == {HELLO, LIBC}:
                break
            await anyio.sleep(0.2)
        assert await _build_times(db) == {HELLO, LIBC}
        assert scheduler._mark_tasks == set()


@pytest.mark.anyio
async def test_a_store_without_a_database_records_nothing(tmp_path: Path) -> None:
    """No database, no recording, no task: the decision path stays silent."""
    scheduler = Scheduler.__new__(Scheduler)
    scheduler.local_store = cast(Any, SimpleNamespace(db=None))
    scheduler._mark_tasks = set()

    scheduler._record_build_closure(HELLO)

    assert scheduler._mark_tasks == set()
