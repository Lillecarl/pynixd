"""Wake promptly when the stable roots move.

`StableRootsWatch` keeps an inotify watch on every directory of the two
stable trees, and sets a dirty event on anything that may have moved
them. The periodic check re-walks everything as usual, so this buys
latency, not correctness: a deployment retargets a link and the mirror
recomputes in milliseconds instead of at the next interval, and a quiet
tree costs no walks at all. A missed event degrades to polling, never to
a stale mirror, because the anchor check still walks the whole trees.

What wakes the check, and why each is safe:

- create/delete/move under either tree: every entry there is a root or
  a directory of roots. A new subdirectory gets its watch before the
  next event is read; an entry created inside it in the gap still wakes
  through the subdirectory's own creation, and the check re-walks the
  whole tree anyway.
- overflow, lost watches, unmount: the watches are re-established and
  the check wakes. A missed event reads as dirty, never as clean.
- the state dir itself: only a tree appearing or leaving counts, so the
  churn under `temproots/` never wakes anyone. This closes the startup
  gap where a tree does not exist yet.

What never wakes it: symlinked directories are not descended into, the
way neither Nix (`findRoots` reads `symlink_status`, `gc.cc:250`) nor
the walk does, and `/proc` cannot be watched at all, so the volatile
roots stay polled like before.
"""

from __future__ import annotations

import errno
import os
from pathlib import Path

import anyio
import structlog
from asyncinotify import Event, Inotify, Mask

from .liveness import STABLE_TREES

log = structlog.get_logger(__name__)

_WATCH_MASK = Mask.CREATE | Mask.DELETE | Mask.MOVE | Mask.DELETE_SELF
"""Link churn plus the watch losing its own directory. Target changes are
replacements, so content and attribute changes carry no signal."""


class DirtyFlag:
    """A re-armable dirty flag: `anyio.Event` fires once, this does not.

    The watcher sets it; the check clears it after waking and before
    working, so a change during the check wakes the next pass at once.
    """

    def __init__(self) -> None:
        self._event = anyio.Event()

    def set(self) -> None:
        self._event.set()

    def is_set(self) -> bool:
        return self._event.is_set()

    async def wait(self) -> None:
        await self._event.wait()

    def clear(self) -> None:
        self._event = anyio.Event()


class StableRootsWatch:
    """An inotify watch over the stable root trees, as a dirty flag."""

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir
        self.trees = [state_dir / name for name in STABLE_TREES]

    async def run(self, dirty: DirtyFlag) -> None:
        """Watch until cancelled, setting *dirty* on every relevant change."""
        with Inotify() as inotify:
            self._watch_all(inotify)
            async for event in inotify:
                if self._handle(event, inotify):
                    dirty.set()

    def _watch_all(self, inotify: Inotify) -> None:
        """(Re)establish every watch: the state dir, then both trees."""
        try:
            inotify.add_watch(self.state_dir, _WATCH_MASK)
        except OSError as exc:
            if exc.errno != errno.ENOENT:
                raise
            log.warning("stable_roots_no_state_dir", state_dir=str(self.state_dir))
        for tree in self.trees:
            self._watch_tree(inotify, tree)

    def _watch_tree(self, inotify: Inotify, root: Path) -> None:
        """A watch on *root* and every real directory under it.

        Missing is fine -- a tree that does not exist yet gets its watch
        when the state dir reports its arrival. Vanishing mid-walk is fine
        too, for the same reason the walk tolerates it.
        """
        if root.is_symlink() or not root.is_dir():
            return
        self._add_watch(inotify, root)
        for dirpath, dirnames, _filenames in os.walk(root):
            for dirname in dirnames:
                sub = Path(dirpath) / dirname
                if not sub.is_symlink() and sub.is_dir():
                    self._add_watch(inotify, sub)

    @staticmethod
    def _add_watch(inotify: Inotify, path: Path) -> None:
        try:
            inotify.add_watch(path, _WATCH_MASK)
        except OSError as exc:
            if exc.errno not in (errno.ENOENT, errno.EACCES):
                raise

    def _handle(self, event: Event, inotify: Inotify) -> bool:
        """True when *event* may have moved the roots."""
        mask = event.mask
        if mask & Mask.Q_OVERFLOW:
            log.warning("stable_roots_watch_overflow")
            self._watch_all(inotify)
            return True
        if mask & (Mask.IGNORED | Mask.DELETE_SELF | Mask.UNMOUNT):
            self._watch_all(inotify)
            return True
        path = event.path
        if path is None:
            return True
        if path.parent == self.state_dir:
            if path.name not in STABLE_TREES:
                return False
            if mask & Mask.ISDIR and mask & (Mask.CREATE | Mask.MOVED_TO):
                self._watch_tree(inotify, path)
            return True
        if mask & Mask.ISDIR and mask & (Mask.CREATE | Mask.MOVED_TO):
            self._watch_tree(inotify, path)
        return True
