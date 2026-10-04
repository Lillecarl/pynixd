"""Background liveness tracking: what Nix calls alive, without asking Nix per pass.

**SPIKE STATUS: measurement only.** Nothing plans from this yet; the
collector still asks Nix. What this builds is the evidence that it could
stop: a roots table plus one closure query that Nix agrees with. Cutover is
a later commit, after the differential test holds zero divergence
sustained -- and no delete runs anywhere until then.

Why this shape: both of Nix's query actions run the same mark phase
(`gc.cc` answers `gcReturnDead` and `gcReturnLive` from one `alive` set),
so asking differently saves nothing, and per-root closures recompute the
shared subgraphs thousands of times. One multi-source closure over the
whole graph visits each node once: measured 1.1 s on 187k paths against
6+ minutes for the trace. The query reads Nix's own tables next to
pynixd's two, so the only Python logic is enumerating root links -- the
closure itself stays in SQL, where there is nothing to diverge.

Two tables, two refresh policies. Stable roots (`gcroots`, `profiles`)
change with deployments: diffed against the table, recompute only on
change. Volatile roots (`/proc`, `temproots`) change constantly and never
land in the table at all; they join each closure query as a parameter.
Typical pass cost is the walks plus three cheap queries (roots diff,
fingerprint, skip check); the closure runs only when something moved.

What it deliberately does not mirror: unlinking stale `gcroots/auto`
links (Nix owns that mutation; the mirror only skips them), and censoring
(a tracker has no untrusted reader).
"""

from __future__ import annotations

import errno
import json
import os
import re
import sqlite3
import time
from contextlib import closing
from pathlib import Path

import structlog

from pynixd.db_migrations import LIVENESS_ROOT_TABLE, LIVENESS_TABLE

log = structlog.get_logger(__name__)

_STORE_BASENAME = re.compile(r"^[0-9a-z]{32}-")
_MAPS_PATH = re.compile(r"^\s*\S+\s+\S+\s+\S+\s+\S+\s+\S+\s+(/\S+)\s*$")

STABLE_TREES = {"gcroots": "gcroot", "profiles": "profile"}
"""The stable root trees under the state dir, and the kind each names.

Both, because Nix scans both itself: `findRootsNoTemp` reads `gcroots`
and `profiles` (`gc.cc:309`), and no profile path reaches the collector
through `gcroots/auto` -- `addIndirectRoot` serves `--add-root
--indirect`, not profiles. A watcher that misses either tree goes blind
on exactly the deployments that move liveness, so the walk and the watch
below read this one constant and cannot disagree about it."""


def _suppressed(error: OSError) -> bool:
    """The failures Nix walks past while finding roots (`gc.cc`)."""
    return error.errno in (errno.ENOENT, errno.EACCES, errno.ENOTDIR, errno.ESRCH)


def _readlink(path: Path) -> Path | None:
    """The link target, or `None` when it went away underneath the walk."""
    try:
        return Path(os.readlink(path))
    except OSError as exc:
        if _suppressed(exc):
            return None
        raise


def _walk_link(link: Path, target: Path, found: dict[str, tuple[str, str]], kind: str, store_dir: str) -> None:
    """One symlink: a root when it names the store, else one indirection.

    Nix resolves every target outside the store the same way (`gc.cc:262`),
    absolute or relative: made absolute against the link, and when that names
    a link into the store the store path is the root, attributed to the
    intermediate. The booted and current system links live through this arm --
    `/run/booted-system` is absolute and outside the store, and the store path
    behind it roots tens of thousands of paths. Stale links simply name
    nothing: Nix unlinks a stale link under `gcroots/auto` here, and the
    mirror never mutates the roots it reads.
    `found` maps the link -- or the intermediate, for an indirect root --
    to `(target, kind)`.
    """
    if target.is_absolute() and str(target).startswith(store_dir + "/"):
        found[str(link)] = (str(target), kind)
        return
    resolved = target if target.is_absolute() else link.parent / target
    if not resolved.exists():
        return
    if not resolved.is_symlink():
        return
    second = _readlink(resolved)
    if second is not None and second.is_absolute() and str(second).startswith(store_dir + "/"):
        found[str(resolved)] = (str(second), kind)


def _walk_tree(tree: Path, found: dict[str, tuple[str, str]], kind: str, store_dir: str) -> None:
    """`findRoots` over one directory: links, indirect links, plain files."""
    try:
        entries = list(os.scandir(tree))
    except OSError as exc:
        if _suppressed(exc):
            return
        raise
    for entry in entries:
        try:
            if entry.is_symlink():
                target = _readlink(Path(entry.path))
                if target is not None:
                    _walk_link(Path(entry.path), target, found, kind, store_dir)
            elif entry.is_dir(follow_symlinks=False):
                _walk_tree(Path(entry.path), found, kind, store_dir)
            elif entry.is_file(follow_symlinks=False):
                if _STORE_BASENAME.match(entry.name):
                    found[entry.path] = (f"{store_dir}/{entry.name}", kind)
        except OSError as exc:
            if not _suppressed(exc):
                raise


def _walk_proc_link(path: Path, roots: set[str]) -> None:
    target = _readlink(path)
    if target is not None and target.is_absolute():
        roots.add(str(target))


def _walk_proc(pid: Path, roots: set[str], store_dir: str) -> None:
    """One `/proc/<pid>`: exe, cwd, fds, mapped files, environment matches."""
    _walk_proc_link(pid / "exe", roots)
    _walk_proc_link(pid / "cwd", roots)
    try:
        fds = list(os.scandir(pid / "fd"))
    except OSError as exc:
        if _suppressed(exc):
            return
        raise
    for fd in fds:
        if fd.name.startswith("."):
            continue
        _walk_proc_link(Path(fd.path), roots)
    try:
        maps = (pid / "maps").read_text(errors="replace").splitlines()
    except OSError as exc:
        if _suppressed(exc):
            return
        raise
    for line in maps:
        match = _MAPS_PATH.match(line)
        if match is not None:
            roots.add(match.group(1))
    try:
        environ = (pid / "environ").read_bytes()
    except OSError as exc:
        if _suppressed(exc):
            return
        raise
    pattern = re.compile(re.escape(store_dir).encode() + rb"/[0-9a-z]+[0-9a-zA-Z+\-._?=]*")
    roots.update(match.decode() for match in pattern.findall(environ))


def _walk_runtime_roots(proc_dir: Path, roots: set[str], store_dir: str) -> None:
    """`findRuntimeRootsUnchecked`, without the censoring."""
    try:
        entries = list(os.scandir(proc_dir))
    except OSError as exc:
        if _suppressed(exc):
            return
        raise
    for entry in entries:
        if entry.name.isdigit():
            try:
                _walk_proc(Path(entry.path), roots, store_dir)
            except OSError as exc:
                if not _suppressed(exc):
                    raise
    for name in ("modprobe", "fbsplash", "poweroff_cmd"):
        try:
            content = (proc_dir / "sys" / "kernel" / name).read_text().strip()
        except OSError as exc:
            if _suppressed(exc):
                continue
            raise
        if content:
            roots.add(content)


def _walk_temp_roots(state_dir: Path, roots: set[str]) -> None:
    """Every path named by the temporary roots files.

    One file per process, its held paths NUL-separated (`gc.cc:163`).
    Nix unlinks a dead owner's file while reading the directory
    (`gc.cc:209`); the mirror never mutates the roots it reads, so that
    file still seeds until Nix's own pass removes it. That errs toward
    keeping, which is the safe direction for a set collected by complement.
    """
    try:
        entries = list(os.scandir(state_dir / "temproots"))
    except OSError as exc:
        if _suppressed(exc):
            return
        raise
    for entry in entries:
        if entry.name.startswith(".") or not entry.is_file(follow_symlinks=False):
            continue
        try:
            content = Path(entry.path).read_bytes()
        except OSError as exc:
            if _suppressed(exc):
                continue
            raise
        roots.update(part.decode(errors="replace") for part in content.split(b"\x00") if part.strip())


def walk_stable(state_dir: Path, store_dir: str) -> dict[str, tuple[str, str]]:
    """The deployment roots: `{link: (target, kind)}` under `gcroots` and `profiles`.

    Compared against the roots table on every pass; only a difference
    recomputes anything. A regular file names its store basename, the way
    `findRoots` parses it against the store directory rather than the link.
    """
    found: dict[str, tuple[str, str]] = {}
    for name, kind in STABLE_TREES.items():
        _walk_tree(state_dir / name, found, kind, store_dir)
    return found


def walk_volatile(state_dir: Path, store_dir: str, proc_dir: Path | None = None) -> set[str]:
    """The living roots: processes and temporary roots, fresh every pass.

    Never stored: they change constantly, and writing them would churn the
    table for no reader. They join each closure query as a parameter.
    """
    targets: set[str] = set()
    _walk_runtime_roots(proc_dir if proc_dir is not None else Path("/proc"), targets, store_dir)
    _walk_temp_roots(state_dir, targets)
    prefix = store_dir + "/"
    return {target for target in targets if target.startswith(prefix)}


QUERY_LIVE_SET = f"""
WITH RECURSIVE closure(id) AS (
    SELECT vp.id FROM ValidPaths vp
    WHERE vp.path IN (
        SELECT target FROM {LIVENESS_ROOT_TABLE}
        UNION
        SELECT value FROM json_each(?)
    )
    UNION
    SELECT r.reference FROM closure c JOIN Refs r ON c.id = r.referrer
    UNION
    SELECT deriver_vp.id FROM closure c
    JOIN ValidPaths current_vp ON c.id = current_vp.id
    JOIN ValidPaths deriver_vp ON current_vp.deriver = deriver_vp.path
    WHERE current_vp.deriver IS NOT NULL
    UNION
    SELECT r.reference FROM closure c
    JOIN ValidPaths current_vp ON c.id = current_vp.id
    JOIN ValidPaths deriver_vp ON current_vp.deriver = deriver_vp.path
    JOIN Refs r ON deriver_vp.id = r.referrer
    WHERE current_vp.deriver IS NOT NULL
)
SELECT path FROM ValidPaths WHERE id IN (SELECT id FROM closure)
"""


def query_live_set(db_path: Path, volatile: set[str]) -> set[str]:
    """The live set: one closure over the table roots plus `volatile`.

    Read-only, and the only graph query in the design: stable roots come
    from the table, living roots join as a parameter. Seconds on hundreds
    of thousands of paths, where per-root closures take minutes.
    """
    with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)) as conn:
        return {row[0] for row in conn.execute(QUERY_LIVE_SET, (json.dumps(sorted(volatile)),))}


def db_fingerprint(db_path: Path) -> tuple[int, int]:
    """`(row count, max id)`: any add moves the max, any delete the count.

    Ids never reuse, so the pair answers whether the adjacency changed
    without reading it. Cheap enough to ask on every poll.
    """
    with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)) as conn:
        count, maximum = conn.execute("SELECT COUNT(*), COALESCE(MAX(id), 0) FROM ValidPaths").fetchone()
    return (int(count), int(maximum))


def refresh_roots(db_path: Path, stable: dict[str, tuple[str, str]]) -> bool:
    """Reconcile the roots table with the walked links. Returns dirtiness.

    Links the walk no longer sees leave; new and retargeted links land.
    Unchanged links are untouched, so an unchanged deployment writes
    nothing. `True` means the roots moved and the live set must recompute.
    """
    with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)) as conn:
        current = {
            row[0]: (row[1], row[2]) for row in conn.execute(f"SELECT link, target, kind FROM {LIVENESS_ROOT_TABLE}")
        }
    if current == stable:
        return False
    with closing(sqlite3.connect(db_path)) as conn, conn:
        for link in current:
            if link not in stable:
                conn.execute(f"DELETE FROM {LIVENESS_ROOT_TABLE} WHERE link = ?", (link,))
        conn.executemany(
            f"INSERT OR REPLACE INTO {LIVENESS_ROOT_TABLE} (link, target, kind) VALUES (?, ?, ?)",
            [
                (link, target, kind)
                for link, (target, kind) in sorted(stable.items())
                if current.get(link) != (target, kind)
            ],
        )
    return True


def write_snapshot(db_path: Path, live: set[str], epoch: int) -> None:
    """A complete liveness snapshot tagged with `epoch`, in one transaction.

    Atomicity is the whole leak argument: the replace commits at once, so a
    pass that dies leaves the previous complete snapshot, never a partial
    epoch. Readers take the newest epoch; the prune drops anything older,
    which only an unclean recovery could leave behind. Dead needs no rows:
    it is the complement against `ValidPaths`.
    """
    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.execute(f"DELETE FROM {LIVENESS_TABLE}")
        conn.executemany(
            f"INSERT INTO {LIVENESS_TABLE} (path, epoch) VALUES (?, ?)",
            [(path, epoch) for path in sorted(live)],
        )


def read_snapshot(db_path: Path) -> tuple[set[str], int] | None:
    """The newest snapshot: `(live paths, epoch)`, or `None` when empty."""
    with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)) as conn:
        row = conn.execute(f"SELECT COALESCE(MAX(epoch), -1) FROM {LIVENESS_TABLE}").fetchone()
        epoch = int(row[0])
        if epoch < 0:
            return None
        live = {r[0] for r in conn.execute(f"SELECT path FROM {LIVENESS_TABLE} WHERE epoch = ?", (epoch,))}
    return (live, epoch)


class RootsTracker:
    """The mirror: roots table current, closure in one query, answer snapshotted.

    `refresh` walks the stable links and diffs the table, walks the living
    roots fresh, and recomputes only when something moved: changed links, a
    changed volatile set, or a changed store. Otherwise the previous live
    set stands, and the pass costs two walks plus three cheap queries. No
    daemon round trip anywhere in it. `differential` compares against what
    Nix answers for the same store, which is the gate the spike must pass
    before anything plans from this.
    """

    def __init__(self, state_dir: Path, store_dir: str, db_path: Path, proc_dir: Path | None = None) -> None:
        self.state_dir = state_dir
        self.store_dir = store_dir
        self.db_path = db_path
        self.proc_dir = proc_dir
        self.fingerprint: tuple[int, int] | None = None
        self.last_volatile: set[str] = set()
        self.live: set[str] = set()
        self.epoch = 0

    def refresh(self) -> set[str]:
        """A liveness pass: sync roots, recompute on change, snapshot."""
        dirty = refresh_roots(self.db_path, walk_stable(self.state_dir, self.store_dir))
        volatile = walk_volatile(self.state_dir, self.store_dir, self.proc_dir)
        fingerprint = db_fingerprint(self.db_path)
        if not dirty and volatile == self.last_volatile and fingerprint == self.fingerprint and self.live:
            return self.live
        self.live = query_live_set(self.db_path, volatile)
        self.epoch = int(time.time())
        write_snapshot(self.db_path, self.live, self.epoch)
        self.last_volatile = volatile
        self.fingerprint = fingerprint
        return self.live

    def differential(self, nix_live: set[str]) -> tuple[set[str], set[str]]:
        """`(only_tracker, only_nix)`: empty on both sides is agreement.

        Compared as strings in both directions, because a divergence either
        way is a fact about the mirror, not about who is right.
        """
        mine = set(self.live)
        theirs = set(nix_live)
        return (mine - theirs, theirs - mine)
