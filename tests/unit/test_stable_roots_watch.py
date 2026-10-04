"""The watcher wakes the check when the stable roots move, and only then.

`gc.cc:309` scans `gcroots` and `profiles`, and no profile path arrives
through `gcroots/auto`, so both trees are watched: the profiles case
below is the regression test for watching one tree and going blind on
the other. Events run through real inotify on a lab state dir; only the
overflow path is synthesised, because overflowing a kernel queue on
purpose is not a test.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import anyio
import pytest
from asyncinotify import Event, Inotify, Mask

from pynixd.liveness_watch import DirtyFlag, StableRootsWatch

TARGET = "/nix/store/00000000000000000000000000000000-a"


def _lab(state: Path) -> tuple[Path, Path]:
    """A state dir with both trees and one link each, like a Nix system."""
    auto = state / "gcroots" / "auto"
    auto.mkdir(parents=True)
    profiles = state / "profiles"
    profiles.mkdir(parents=True)
    (auto / "direct").symlink_to(TARGET)
    (profiles / "profile").symlink_to(TARGET)
    return auto, profiles


async def _wakes(state: Path, action: Callable[[], None]) -> bool:
    """Whether *action* sets the dirty event within ten seconds."""
    watch = StableRootsWatch(state)
    dirty = DirtyFlag()
    async with anyio.create_task_group() as tg:
        tg.start_soon(watch.run, dirty)
        await anyio.sleep(0.2)
        action()
        with anyio.move_on_after(10):
            await dirty.wait()
        tg.cancel_scope.cancel()
    return dirty.is_set()


async def _stays_quiet(state: Path, action: Callable[[], None]) -> bool:
    """Whether *action* leaves the dirty event unset for half a second."""
    watch = StableRootsWatch(state)
    dirty = DirtyFlag()
    async with anyio.create_task_group() as tg:
        tg.start_soon(watch.run, dirty)
        await anyio.sleep(0.2)
        action()
        with anyio.move_on_after(0.5):
            await dirty.wait()
        tg.cancel_scope.cancel()
    return not dirty.is_set()


@pytest.mark.anyio
async def test_a_created_link_wakes(tmp_path: Path) -> None:
    auto, _profiles = _lab(tmp_path)

    assert await _wakes(tmp_path, lambda: (auto / "new").symlink_to(TARGET))


@pytest.mark.anyio
async def test_a_deleted_link_wakes(tmp_path: Path) -> None:
    auto, _profiles = _lab(tmp_path)

    assert await _wakes(tmp_path, lambda: (auto / "direct").unlink())


@pytest.mark.anyio
async def test_a_retargeted_link_wakes(tmp_path: Path) -> None:
    """A replace is a delete plus a create; either half wakes."""
    auto, _profiles = _lab(tmp_path)

    def retarget() -> None:
        (auto / "direct").unlink()
        (auto / "direct").symlink_to(TARGET)

    assert await _wakes(tmp_path, retarget)


@pytest.mark.anyio
async def test_a_new_subdirectory_is_watched(tmp_path: Path) -> None:
    """The watch follows the tree down: a link under a new dir wakes."""
    auto, _profiles = _lab(tmp_path)

    def nest() -> None:
        sub = auto / "sub"
        sub.mkdir()
        (sub / "nested").symlink_to(TARGET)

    assert await _wakes(tmp_path, nest)


@pytest.mark.anyio
async def test_the_profiles_tree_is_watched(tmp_path: Path) -> None:
    """Profiles are their own root source (`gc.cc:309`), watched like `gcroots`."""
    _auto, profiles = _lab(tmp_path)

    assert await _wakes(tmp_path, lambda: (profiles / "profile").unlink())


@pytest.mark.anyio
async def test_churn_outside_the_trees_stays_quiet(tmp_path: Path) -> None:
    """`temproots/` churn must never wake the check."""
    _lab(tmp_path)
    temproots = tmp_path / "temproots"
    temproots.mkdir()

    def churn() -> None:
        (temproots / "123").write_text(f"{TARGET}\n")  # noqa: ASYNC240 -- the event under test

    assert await _stays_quiet(tmp_path, churn)


def test_overflow_rescans_and_wakes(tmp_path: Path) -> None:
    """A missed event reads as dirty, never as clean."""
    _lab(tmp_path)
    watch = StableRootsWatch(tmp_path)

    with Inotify() as inotify:
        assert watch._handle(Event(watch=None, mask=Mask.Q_OVERFLOW, cookie=0, name=None), inotify) is True
