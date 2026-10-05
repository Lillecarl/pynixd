"""Build-kind observations never freshen. Issue #65.

A binary that stays live must stop keeping its entire build closure
fresh for ever: every build that names the compiler records the use in
the build table, and the planner -- which reads only the access table
-- keeps judging the compiler by age.

The test backdates an old access row for the compiler, records repeated
build-kind observations the way nightly builds would, and asserts the
dry-run still names it. A runtime observation of the product spares it,
which proves the split and not just the absence of marks.

Perturbation: flush the build observations into the access table and the
compiler reads fresh, so the plan spares it.
"""

from __future__ import annotations

import time
from pathlib import Path

import aiosqlite

from nix_daemon_protocol.ids import StoreId
from pynixd import Server
from pynixd.daemon_extensions import PynixdGCAction
from pynixd.db_migrations import PATH_ACCESS_TABLE
from pynixd.gc import Collector
from pynixd.store import LocalDBStore
from tests.conftest import NIX_BIN, make_test_spec, run_subproc

STALE_AGE = 7200
MAX_AGE = 3600


async def _add(store_path: Path, work: Path, name: str, text: str) -> str:
    source = work / name
    source.write_text(text)  # noqa: ASYNC240 -- test setup
    _rc, stdout, _stderr, _both = await run_subproc(
        [str(NIX_BIN), "store", "add-path", "--store", str(store_path), str(source)],
        verbose=False,
    )
    return stdout.strip()


async def _backdate(store_path: Path, paths: set[str], age: int) -> None:
    (db,) = list(store_path.rglob("db.sqlite"))
    async with aiosqlite.connect(db) as conn:
        await conn.executemany(
            f"INSERT OR REPLACE INTO {PATH_ACCESS_TABLE} (path, lastReferencedAt) VALUES (?, ?)",
            [(path, int(time.time()) - age) for path in sorted(paths)],
        )
        await conn.commit()


async def test_build_observations_do_not_freshen(tmp_path: Path) -> None:
    store_path = tmp_path / "store"
    store_path.mkdir()
    compiler = await _add(store_path, tmp_path, "compiler.txt", "old, and every build names it\n")
    product = await _add(store_path, tmp_path, "product.txt", "old, and clients read it\n")

    spec = make_test_spec(
        store_id="local",
        store_path=store_path,
        no_probe=True,
        gc_max_age=MAX_AGE,
        gc_allow_execute=True,
    )
    async with Server(
        stores={StoreId("local"): LocalDBStore(spec)},
        ssh_port=None,
        http_port=None,
    ) as server:
        await _backdate(store_path, {compiler, product}, STALE_AGE)
        db = getattr(server.ctx.local_store, "db", None)
        assert db is not None
        # Nightly builds name the compiler, over and over. Build-kind.
        for _ in range(3):
            db.mark_paths([compiler], kind="build")
        await db.flush_references()
        # A client reads the product. Runtime-kind.
        db.mark_paths([product])
        await db.flush_references()

        resp = await Collector(server.ctx).run(PynixdGCAction.DRY_RUN)

    assert {str(path) for path in resp.store_paths} == {compiler}
