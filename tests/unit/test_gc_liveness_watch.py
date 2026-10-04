"""The sidecar compares the mirror against Nix and reports, deleting nothing.

`check` refreshes the tracker, asks Nix what is alive, and answers whether
the two agree. It also files the verdict in the streak table, so "sustained"
is a number the cutover decision reads instead of a feeling about the logs.
The daemon runs it on `gc_liveness_interval`; this file runs it against a
stub tracker and a fake store, because the mirror's own agreement with Nix
is `test_liveness_tracker`'s subject, not this one's.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from nix_daemon_protocol import GCAction
from nix_daemon_protocol.store_path import StorePath
from pynixd.db_migrations import apply_migrations
from pynixd.gc import LivenessWatch
from pynixd.liveness import read_streak
from pynixd.store_layout import StoreLayout

A = "/nix/store/00000000000000000000000000000000-a"
B = "/nix/store/11111111111111111111111111111111-b"


@dataclass
class StubTracker:
    """The `RootsTracker` shape, with a preset live set and no filesystem."""

    live: set[str] = field(default_factory=set)
    refreshed: int = 0
    db_path: Path | None = None

    def refresh(self) -> set[str]:
        self.refreshed += 1
        return set(self.live)

    def differential(self, nix_live: set[str]) -> tuple[set[str], set[str]]:
        mine = set(self.live)
        theirs = set(nix_live)
        return (mine - theirs, theirs - mine)


@dataclass
class _Live:
    paths_deleted: set[Any]


@dataclass
class FakeLocal:
    """A store that answers `RETURN_LIVE` and nothing else."""

    nix_live: set[str] = field(default_factory=set)
    calls: list[str] = field(default_factory=list)

    async def call(self, request: Any, **_kwargs: Any) -> Any:
        self.calls.append(str(request.action))
        if request.action == GCAction.RETURN_LIVE:
            return _Live({StorePath(path) for path in self.nix_live})
        raise AssertionError(f"unexpected call {request.action}")


def _watch(tracker_live: set[str], nix_live: set[str]) -> tuple[LivenessWatch, StubTracker, FakeLocal]:
    tracker = StubTracker(live=set(tracker_live))
    local = FakeLocal(nix_live=set(nix_live))
    return LivenessWatch(tracker, local), tracker, local  # type: ignore[arg-type] -- stub tracker, fake store


@pytest.mark.anyio
async def test_agreement_answers_true_and_asks_nix_once():
    watch, tracker, local = _watch({A, B}, {A, B})

    assert await watch.check() is True

    assert tracker.refreshed == 1
    assert local.calls == [str(GCAction.RETURN_LIVE)]


@pytest.mark.anyio
async def test_divergence_either_way_answers_false():
    assert await _watch({A, B}, {A})[0].check() is False
    assert await _watch({A}, {A, B})[0].check() is False


async def _migrated_db(tmp_path: Path) -> Path:
    """A store database with pynixd's tables, and nothing of Nix's."""
    db = tmp_path / "db.sqlite"
    # The migration opens `mode=rw`, which never creates: the file first.
    with closing(sqlite3.connect(db)):
        pass
    assert (await apply_migrations(db, read_only=False)).usable
    return db


@pytest.mark.anyio
async def test_agreement_streak_counts_consecutive_checks(tmp_path: Path) -> None:
    """Two agreements file streak one and two, with the set size beside them.

    The second check runs on a new watch over the same file: the streak
    lives in the database, not in the process, so a daemon restart keeps it.

    Perturbation: stop recording in `check` and both reads come back `None`.
    """
    db = await _migrated_db(tmp_path)
    local = FakeLocal(nix_live={A, B})

    assert await LivenessWatch(StubTracker(live={A, B}, db_path=db), local).check() is True  # type: ignore[arg-type] -- stub tracker, fake store
    assert await LivenessWatch(StubTracker(live={A, B}, db_path=db), local).check() is True  # type: ignore[arg-type] -- stub tracker, fake store

    streak = read_streak(db)
    assert streak is not None
    assert (streak.agreements, streak.checks, streak.divergences, streak.live) == (2, 2, 0, 2)


@pytest.mark.anyio
async def test_divergence_resets_the_streak_and_counts_it(tmp_path: Path) -> None:
    """Agree, agree, diverge, agree: the file reads one, four, one, two.

    The divergence is the reset signal the cutover gate watches for; its
    count beside the streak says how often the mirror flapped.

    Perturbation: stop resetting on disagreement and the last read is four.
    """
    db = await _migrated_db(tmp_path)
    local = FakeLocal(nix_live={A, B})

    def watch(live: set[str]) -> LivenessWatch:
        return LivenessWatch(StubTracker(live=set(live), db_path=db), local)  # type: ignore[arg-type] -- stub tracker, fake store

    assert await watch({A, B}).check() is True
    assert await watch({A, B}).check() is True
    local.nix_live = {A}
    assert await watch({A, B}).check() is False
    local.nix_live = {A, B}
    assert await watch({A, B}).check() is True

    streak = read_streak(db)
    assert streak is not None
    assert (streak.agreements, streak.checks, streak.divergences, streak.live) == (1, 4, 1, 2)


def test_from_local_needs_a_layout():
    assert LivenessWatch.from_local(object()) is None  # type: ignore[arg-type] -- no layout


def test_from_local_mirrors_the_served_store(tmp_path: Path) -> None:
    """The tracker reads the layout's state and names its path prefix."""
    layout = StoreLayout(
        store_dir=Path("/nix/store"),
        real_store_dir=Path("/nix/store"),
        state_dir=tmp_path / "state",
        relocated=False,
    )

    @dataclass
    class LaidOut:
        layout: StoreLayout

        async def call(self, request: Any, **_kwargs: Any) -> Any:
            raise AssertionError("no call on construction")

    watch = LivenessWatch.from_local(LaidOut(layout=layout))  # type: ignore[arg-type] -- layout only

    assert watch is not None
    assert watch.tracker.state_dir == layout.state_dir
    assert watch.tracker.store_dir == "/nix/store"
    assert watch.tracker.db_path == layout.state_dir / "db" / "db.sqlite"
