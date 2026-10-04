"""The sidecar compares the mirror against Nix and reports, deleting nothing.

`check` refreshes the tracker, asks Nix what is alive, and answers whether
the two agree. The daemon runs it on `gc_liveness_interval`; this file runs
it against a stub tracker and a fake store, because the mirror's own
agreement with Nix is `test_liveness_tracker`'s subject, not this one's.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from nix_daemon_protocol import GCAction
from nix_daemon_protocol.store_path import StorePath
from pynixd.gc import LivenessWatch
from pynixd.store_layout import StoreLayout

A = "/nix/store/00000000000000000000000000000000-a"
B = "/nix/store/11111111111111111111111111111111-b"


@dataclass
class StubTracker:
    """The `RootsTracker` shape, with a preset live set and no filesystem."""

    live: set[str] = field(default_factory=set)
    refreshed: int = 0

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
