"""The liveness mirror reads roots the way Nix does, and closes them in SQL.

Root enumeration mirrors `gc.cc:231` (`findRoots`) and `local-gc.cc`
(`/proc` scan): links, indirect links, plain files, processes, temporary
roots. Existence follows the final target, exactly like Nix's `pathExists`
check: a link through a missing target names nothing, in both. The closure
stays one recursive CTE over Nix's own tables, so the only Python graph
logic is the oracle below, which cross-checks the query on fixtures small
enough to read. Production agreement with Nix is `test_liveness_tracker`;
this file proves the pieces.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from typing import TYPE_CHECKING

from pynixd.db_migrations import LIVENESS_ROOT_TABLE, LIVENESS_TABLE
from pynixd.liveness import (
    query_live_set,
    read_snapshot,
    refresh_roots,
    walk_stable,
    walk_volatile,
    write_snapshot,
)

if TYPE_CHECKING:
    from pathlib import Path

HASH_A = "00000000000000000000000000000001"
HASH_B = "00000000000000000000000000000002"
HASH_C = "00000000000000000000000000000003"
HASH_D = "00000000000000000000000000000004"


def _layout(root: Path) -> tuple[Path, Path, Path, dict[str, str]]:
    """A state dir with every root shape, all targets real files.

    Targets exist because existence follows the final target, in Nix and
    here: a link through a missing target names nothing. `proc` is a fake
    process tree; the last mapping holds the store, its paths, and the
    environment block each name.
    """
    store = root / "store"
    store.mkdir()
    paths = {}
    for hash_part, name in ((HASH_A, "a"), (HASH_B, "b"), (HASH_C, "c"), (HASH_D, "d")):
        path = store / f"{hash_part}-{name}"
        path.write_text("x")  # noqa: ASYNC240 -- test setup
        paths[name] = str(path)

    state = root / "state"
    (state / "gcroots" / "auto" / "sub").mkdir(parents=True)
    (state / "profiles").mkdir(parents=True)
    (state / "temproots").mkdir(parents=True)
    proc = root / "proc"
    (proc / "123" / "fd").mkdir(parents=True)

    auto = state / "gcroots" / "auto"
    (auto / "direct").symlink_to(paths["a"])
    (auto / "dangling").symlink_to(root / "nowhere")
    (auto / "sub" / "nested").symlink_to(paths["b"])
    (auto / "indirect").symlink_to("rel-target")
    (auto / "rel-target").symlink_to(paths["c"])
    (state / "profiles" / "profile").symlink_to(paths["a"])
    # NUL-separated, the way Nix writes them (`gc.cc:163`): one file holds
    # two roots, and newlines never appear.
    (state / "temproots" / "999").write_bytes(  # noqa: ASYNC240 -- test setup
        f"{paths['b']}\x00{paths['d']}\x00not-a-path\x00".encode()
    )

    pid = proc / "123"
    (pid / "exe").symlink_to(f"{paths['c']}-prog")
    (pid / "cwd").symlink_to("/tmp")
    (pid / "fd" / "0").symlink_to("/dev/null")
    (pid / "fd" / "3").symlink_to(paths["a"])
    (pid / "maps").write_text(  # noqa: ASYNC240 -- test setup
        "7f000000-7f100000 r--p 00000000 00:01 1 /lib/libc.so\n"
        f"7f100000-7f200000 r--p 00000000 00:01 2 {paths['b']}\n"
        "7f200000-7f300000 r--p 00000000 00:01 3\n"
    )
    (pid / "environ").write_bytes(b"PATH=/bin\x00NIX_STORE=" + f"{paths['a']}-env".encode() + b"\x00")
    (proc / "notapid").mkdir()
    (proc / "sys" / "kernel").mkdir(parents=True)
    (proc / "sys" / "kernel" / "modprobe").write_text(f"{paths['c']}-modprobe\n")  # noqa: ASYNC240 -- test setup
    return state, proc, auto, paths


def _reference_bfs(seeds: set[str], refs: dict[str, set[str]], derivers: dict[str, str | None]) -> set[str]:
    """The oracle: the same three branches as the CTE, walked in Python.

    References transitively, derivers, and the references of derivers. Any
    disagreement with `QUERY_LIVE_SET` on a fixture is a fault in one of
    the two, and the fixture is small enough to tell which.
    """
    seen: set[str] = set()
    queue = list(seeds)
    while queue:
        node = queue.pop()
        if node in seen:
            continue
        seen.add(node)
        deriver = derivers.get(node)
        for edge in list(refs.get(node, ())) + ([deriver] if deriver else []):
            if edge not in seen:
                queue.append(edge)
    return seen


def test_stable_walk_names_every_link_shape(tmp_path: Path) -> None:
    state, _proc, auto, paths = _layout(tmp_path)

    found = walk_stable(state, str(tmp_path / "store"))

    assert found == {
        str(auto / "direct"): (paths["a"], "gcroot"),
        str(auto / "sub" / "nested"): (paths["b"], "gcroot"),
        # The intermediate only: Nix attributes an indirect root to the
        # link it resolved through (`gc.cc:275`), not to the link that
        # named it. Retargeting either still changes a row, which is the
        # signal the refresh diffs on.
        str(auto / "rel-target"): (paths["c"], "gcroot"),
        str(state / "profiles" / "profile"): (paths["a"], "profile"),
    }


def test_stable_walk_follows_an_absolute_link_outside_the_store(tmp_path: Path) -> None:
    """`/run/booted-system` is absolute and outside the store, and Nix follows it.

    `gc.cc:262` resolves every target outside the store against the link,
    absolute or relative. Only a relative target took that path here, so
    the booted and current system links rooted nothing and the production
    mirror diverged by tens of thousands of paths.

    Perturbation: restore the early return for absolute targets and this fails.
    """
    state = tmp_path / "state"
    (state / "gcroots" / "auto").mkdir(parents=True)
    outside = tmp_path / "run"
    outside.mkdir()
    store = tmp_path / "store"
    store.mkdir()
    target = store / f"{HASH_A}-sys"
    target.write_text("x")  # noqa: ASYNC240 -- test setup
    (outside / "booted-system").symlink_to(target)
    (state / "gcroots" / "auto" / "sys").symlink_to(outside / "booted-system")

    assert walk_stable(state, str(store)) == {
        str(outside / "booted-system"): (str(target), "gcroot"),
    }


def test_volatile_walk_reads_processes_and_temp_roots(tmp_path: Path) -> None:
    """Processes, mappings, environments -- and both NUL-separated temp roots.

    One temp file holds two paths; a line splitter sees one blob and seeds
    neither. Perturbation: split the temp file on lines and `d` leaves this set.
    """
    state, proc, _auto, paths = _layout(tmp_path)

    assert walk_volatile(state, str(tmp_path / "store"), proc) == {
        f"{paths['c']}-prog",
        paths["a"],
        paths["b"],
        paths["d"],
        f"{paths['a']}-env",
        f"{paths['c']}-modprobe",
    }


def _tiny_db(path: Path) -> None:
    """A -> B -> C, and D derives A: every closure branch on four rows."""
    a = "/fake/store/00000000000000000000000000000001-a"
    b = "/fake/store/00000000000000000000000000000002-b"
    c = "/fake/store/00000000000000000000000000000003-c"
    d = "/fake/store/00000000000000000000000000000004-d"
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("CREATE TABLE ValidPaths (id INTEGER PRIMARY KEY, path TEXT UNIQUE, deriver TEXT)")
        conn.execute("CREATE TABLE Refs (referrer INTEGER, reference INTEGER)")
        conn.execute(f"CREATE TABLE {LIVENESS_ROOT_TABLE} (link TEXT PRIMARY KEY, target TEXT, kind TEXT)")
        conn.execute(f"CREATE TABLE {LIVENESS_TABLE} (path TEXT PRIMARY KEY, epoch INTEGER)")
        conn.execute("INSERT INTO ValidPaths VALUES (1, ?, ?)", (a, d))
        conn.execute("INSERT INTO ValidPaths VALUES (2, ?, NULL)", (b,))
        conn.execute("INSERT INTO ValidPaths VALUES (3, ?, NULL)", (c,))
        conn.execute("INSERT INTO ValidPaths VALUES (4, ?, NULL)", (d,))
        conn.execute("INSERT INTO Refs VALUES (1, 2)")
        conn.execute("INSERT INTO Refs VALUES (2, 3)")
        conn.execute("INSERT INTO Refs VALUES (4, 2)")


def test_closure_query_matches_the_oracle(tmp_path: Path) -> None:
    """The CTE and the Python walk agree on references, derivers, and both.

    From `A`: `B` and `C` by references, `D` as the deriver, and `B` again
    as the deriver's reference. Seeding `D` alone must also pull `B` and `C`
    but never `A`: referrers are not references.
    """
    db = tmp_path / "db.sqlite"
    _tiny_db(db)
    a = "/fake/store/00000000000000000000000000000001-a"
    b = "/fake/store/00000000000000000000000000000002-b"
    c = "/fake/store/00000000000000000000000000000003-c"
    d = "/fake/store/00000000000000000000000000000004-d"
    refs = {a: {b}, b: {c}, d: {b}}
    derivers: dict[str, str | None] = {a: d}

    assert query_live_set(db, {a}) == _reference_bfs({a}, refs, derivers) == {a, b, c, d}
    assert query_live_set(db, {d}) == _reference_bfs({d}, refs, derivers) == {b, c, d}


def test_refresh_roots_reports_dirtiness(tmp_path: Path) -> None:
    state, _proc, auto, paths = _layout(tmp_path)
    db = tmp_path / "db.sqlite"
    _tiny_db(db)

    assert refresh_roots(db, walk_stable(state, str(tmp_path / "store"))) is True
    assert refresh_roots(db, walk_stable(state, str(tmp_path / "store"))) is False

    (auto / "direct").unlink()
    assert refresh_roots(db, walk_stable(state, str(tmp_path / "store"))) is True
    with closing(sqlite3.connect(db)) as conn:
        # Three, not four: the indirect link folds into its intermediate's
        # row, so `direct`, `nested` and the profile remain.
        assert conn.execute(f"SELECT COUNT(*) FROM {LIVENESS_ROOT_TABLE}").fetchone()[0] == 3


def test_snapshot_round_trips_and_replaces(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite"
    _tiny_db(db)
    a = "/fake/store/00000000000000000000000000000001-a"
    b = "/fake/store/00000000000000000000000000000002-b"
    c = "/fake/store/00000000000000000000000000000003-c"

    assert read_snapshot(db) is None
    write_snapshot(db, {a, b}, 7)
    assert read_snapshot(db) == ({a, b}, 7)
    write_snapshot(db, {b, c}, 9)
    assert read_snapshot(db) == ({b, c}, 9)
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute(f"SELECT COUNT(*) FROM {LIVENESS_TABLE}").fetchone()[0] == 2
