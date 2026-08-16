"""pynixd records when each store path was last referenced.

The user's design: `registrationTime` is an otherwise unused column, so
refreshing it on every reference turns stock `nix-collect-garbage
--delete-older-than` into an LRU collector for free.

None of it ran. `mark_path` and `mark_paths` had no caller in any project of
this repository, so `pending_references` was always empty,
`flush_references` returned at its first line, and the background task woke
every five seconds to do nothing at all. `LocalDBStore.execute` is the caller
that was missing.

`PynixdPathAccess` is the second half. `registrationTime` says "when this
path entered the store", and `nix path-info --json` reports it as that, so
one number cannot answer both questions afterwards. Issue #166.
"""

from __future__ import annotations

import sqlite3
import time
from typing import TYPE_CHECKING

import pytest

from pynixd.db_migrations import PATH_ACCESS_TABLE
from pynixd.local_store_db import LocalStoreDB
from pynixd.serde import (
    IsValidPathRequest,
    QueryAllValidPathsRequest,
    QueryValidPathsRequest,
    StorePath as SerdeStorePath,
)
from pynixd.store.local_db import referenced_paths

if TYPE_CHECKING:
    from pathlib import Path

HELLO = "/nix/store/00000000000000000000000000000001-hello"
LIBC = "/nix/store/00000000000000000000000000000002-libc"
GONE = "/nix/store/00000000000000000000000000000003-gone"


def _store_with_a_closure(tmp_path: Path) -> Path:
    """A store database where `hello` references `libc`."""
    db_path = tmp_path / "nix" / "var" / "nix" / "db" / "db.sqlite"
    db_path.parent.mkdir(parents=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE ValidPaths ("
            "id INTEGER PRIMARY KEY, path TEXT UNIQUE NOT NULL, "
            "deriver TEXT, registrationTime INTEGER)",
        )
        conn.execute("CREATE TABLE Refs (referrer INTEGER, reference INTEGER)")
        conn.execute("INSERT INTO ValidPaths (id, path, registrationTime) VALUES (1, ?, 0)", (HELLO,))
        conn.execute("INSERT INTO ValidPaths (id, path, registrationTime) VALUES (2, ?, 0)", (LIBC,))
        conn.execute("INSERT INTO Refs (referrer, reference) VALUES (1, 2)")
    return db_path


async def _access_times(db: LocalStoreDB) -> dict[str, int]:
    async with db.execute(f"SELECT path, lastReferencedAt FROM {PATH_ACCESS_TABLE}") as cursor:
        return {str(row[0]): int(row[1]) for row in await cursor.fetchall()}


async def _registration_times(db: LocalStoreDB) -> dict[str, int]:
    async with db.execute("SELECT path, registrationTime FROM ValidPaths") as cursor:
        return {str(row[0]): int(row[1]) for row in await cursor.fetchall()}


class TestReadingThePathsOfARequest:
    """`referenced_paths` reads the declared fields, not a list of operations."""

    def test_a_single_path_field(self) -> None:
        request = IsValidPathRequest(path=SerdeStorePath(path=HELLO))
        assert referenced_paths(request) == {HELLO}

    def test_a_set_of_paths(self) -> None:
        request = QueryValidPathsRequest(
            paths={SerdeStorePath(path=HELLO), SerdeStorePath(path=LIBC)},
        )
        assert referenced_paths(request) == {HELLO, LIBC}

    def test_a_request_that_names_no_path(self) -> None:
        assert referenced_paths(QueryAllValidPathsRequest()) == set()

    def test_an_empty_path_is_not_a_reference(self) -> None:
        """The wire spells "no path" as the empty string, and that is not one."""
        assert referenced_paths(IsValidPathRequest(path=SerdeStorePath(path=""))) == set()

    def test_something_that_is_not_a_request(self) -> None:
        assert referenced_paths(object()) == set()


@pytest.mark.anyio
class TestFlushingTheReferences:
    async def test_a_marked_path_reaches_the_table_with_its_closure(self, tmp_path: Path) -> None:
        """`libc` was never named, and it is referenced because `hello` is."""
        _store_with_a_closure(tmp_path)
        db = await LocalStoreDB.open(tmp_path)
        try:
            db.mark_path(HELLO)
            await db.flush_references()

            assert set(await _access_times(db)) == {HELLO, LIBC}
        finally:
            await db.close()

    async def test_the_registration_time_is_refreshed_as_well(self, tmp_path: Path) -> None:
        """Both, and on purpose. `nix-collect-garbage` reads the Nix column."""
        _store_with_a_closure(tmp_path)
        db = await LocalStoreDB.open(tmp_path)
        try:
            db.mark_path(HELLO)
            await db.flush_references()

            assert all(t > 0 for t in (await _registration_times(db)).values())
        finally:
            await db.close()

    async def test_marking_nothing_writes_nothing(self, tmp_path: Path) -> None:
        _store_with_a_closure(tmp_path)
        db = await LocalStoreDB.open(tmp_path)
        try:
            await db.flush_references()
            assert await _access_times(db) == {}
        finally:
            await db.close()

    async def test_a_second_reference_moves_the_time_forward(self, tmp_path: Path) -> None:
        _store_with_a_closure(tmp_path)
        db = await LocalStoreDB.open(tmp_path)
        try:
            db.mark_path(HELLO)
            await db.flush_references()
            async with db.acquire_conn() as conn:
                await conn.execute(f"UPDATE {PATH_ACCESS_TABLE} SET lastReferencedAt = 1")
                await conn.commit()

            db.mark_path(HELLO)
            await db.flush_references()

            assert all(t > 1 for t in (await _access_times(db)).values())
        finally:
            await db.close()

    async def test_the_pending_set_is_emptied(self, tmp_path: Path) -> None:
        _store_with_a_closure(tmp_path)
        db = await LocalStoreDB.open(tmp_path)
        try:
            db.mark_paths([HELLO, LIBC])
            await db.flush_references()
            assert db.pending_references == set()
        finally:
            await db.close()


@pytest.mark.anyio
class TestAskingTheTable:
    async def test_an_old_path_is_reported_and_a_fresh_one_is_not(self, tmp_path: Path) -> None:
        _store_with_a_closure(tmp_path)
        db = await LocalStoreDB.open(tmp_path)
        try:
            db.mark_path(HELLO)
            await db.flush_references()
            async with db.acquire_conn() as conn:
                await conn.execute(
                    f"UPDATE {PATH_ACCESS_TABLE} SET lastReferencedAt = ? WHERE path = ?",
                    (int(time.time()) - 90_000, LIBC),
                )
                await conn.commit()

            stale = await db.query_paths_not_referenced_since(86_400)

            assert stale is not None
            assert {str(p) for p in stale} == {LIBC}
        finally:
            await db.close()

    async def test_a_path_the_store_no_longer_holds_is_pruned(self, tmp_path: Path) -> None:
        """The join that keeping the table inside Nix's database buys."""
        _store_with_a_closure(tmp_path)
        db = await LocalStoreDB.open(tmp_path)
        try:
            async with db.acquire_conn() as conn:
                await conn.execute(
                    f"INSERT INTO {PATH_ACCESS_TABLE} (path, lastReferencedAt) VALUES (?, 1)",
                    (GONE,),
                )
                await conn.commit()

            removed = await db.prune_path_access()

            assert removed == 1
            assert GONE not in await _access_times(db)
        finally:
            await db.close()

    async def test_pruning_an_untouched_table_removes_nothing(self, tmp_path: Path) -> None:
        _store_with_a_closure(tmp_path)
        db = await LocalStoreDB.open(tmp_path)
        try:
            db.mark_path(HELLO)
            await db.flush_references()
            assert await db.prune_path_access() == 0
        finally:
            await db.close()
