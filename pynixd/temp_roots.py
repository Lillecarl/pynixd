"""The temporary roots of the collector, written by pynixd itself.

A temporary root keeps a store path alive while a client still needs it, and
it goes away when that client does. Nix writes one file for each process, at
`<state>/temproots/<pid>`, and the process holds a write lock on that file
for as long as the roots must live. Each root is one store path and one NUL
byte, appended to the file.

`LocalStore::findTempRoots` reads that directory. A file it can write-lock
belongs to a process that is gone, so the collector deletes the file. A file
it cannot write-lock gives it one root for each path inside. The name of the
file means nothing to the collector, which uses the name for a log line only,
so pynixd is free to write one file for each client session.

**pynixd forwarded `AddTempRoot` to the upstream daemon, and that is the
defect of issue #174.** The root then belonged to the upstream connection,
and pynixd pools those connections between clients. So the root of a client
outlived that client, and a discarded connection dropped the root of a client
that still ran. A root that pynixd writes itself needs no connection at all,
and its life is exactly the life of the client session.

Nix implements the same three steps in `LocalStore::addTempRoot`, in
`src/libstore/gc.cc`, and this module follows that function. The lock is
`flock(2)`, from `src/libstore/unix/pathlocks.cc`.
"""

from __future__ import annotations

import fcntl
import itertools
import os
import socket
import time
from pathlib import Path

import anyio
import structlog
from anyio.to_thread import run_sync

from .store_path import StorePath

log = structlog.get_logger(__name__)

GC_LOCK_FILE = "gc.lock"
GC_SOCKET_PATH = "gc-socket/socket"
TEMP_ROOTS_DIR = "temproots"

# The collector answers one byte for each root that it takes.
COLLECTOR_ACK = b"1"

# How long to wait before pynixd asks the collector again, and how many times.
# The collector is between two states when it refuses the socket: it holds the
# big lock, and it has not made the socket yet. Nix waits 100 ms and tries
# again, with no limit. pynixd gives up after 10 s, because a client waits for
# the answer and a daemon that never answers is worse than an error.
RETRY_DELAY = 0.1
RETRY_LIMIT = 100

_names = itertools.count()


def state_dir(store_path: Path | None) -> Path:
    """The `nix/var/nix` of a store root.

    `LocalStore.ensure_daemon` gives the managed daemon `--store <root>`, and
    `local-fs-store.hh` then puts the state of that store at
    `<root>/nix/var/nix`. The store at `/` is the system one, whose state is
    at `/nix/var/nix`. `resolve_db_path` reads the same layout for the SQLite
    database.
    """
    root = store_path or Path("/")
    if root == Path("/"):
        return Path("/nix/var/nix")
    return root / "nix" / "var" / "nix"


class TempRoots:
    """One temporary roots file, and the paths that it holds.

    One instance belongs to one client session. `close` releases every root
    of that session at once, and it is the reason this class exists: there is
    no operation in the daemon protocol that removes a temporary root, so the
    only way to release one is to let go of the file.

    pynixd degrades to nothing when it cannot write the directory, which
    happens when it serves the system store as an unprivileged user. The
    client then gets the same answer that a non-admin client got before: the
    operation reports success and adds no root. A collector that runs at that
    moment can delete the path, so this is not correct; it is what pynixd can
    do without write access, and it says so in the log.
    """

    def __init__(self, state: Path) -> None:
        """Prepare a roots file under `state`, and open nothing yet."""
        self.state = state
        self.dir = state / TEMP_ROOTS_DIR
        self.path = self.dir / f"pynixd-{os.getpid()}-{next(_names)}"
        self._fd: int | None = None
        self._socket: socket.socket | None = None
        self._disabled = False
        self._lock = anyio.Lock()

    async def add(self, path: str | StorePath) -> None:
        """Hold `path` against the collector until `close`."""
        root = str(StorePath(str(path)))
        async with self._lock:
            if self._disabled:
                return
            await run_sync(self._add, root)

    async def close(self) -> None:
        """Release every root of this session."""
        async with self._lock:
            await run_sync(self._close)

    # ── The blocking half ────────────────────────────────────────────
    #
    # Each of these runs in a worker thread. They are short: an `open`, a
    # non-blocking `flock` and a `write` on a local file system. The socket
    # is the one part that waits, and only while the collector runs.

    def _add(self, root: str) -> None:
        try:
            self._add_or_raise(root)
        except OSError as exc:
            self._disabled = True
            log.warning(
                "temp_root_unavailable",
                temp_roots_file=str(self.path),
                error=str(exc),
                detail=(
                    "pynixd cannot write the temporary roots of this store, so it holds no "
                    "path against the collector. Give pynixd write access to the state "
                    "directory of the store, or run the collector while pynixd is stopped."
                ),
            )

    def _add_or_raise(self, root: str) -> None:
        if self._fd is None:
            self._create()
        fd = self._fd
        if fd is None:
            raise RuntimeError("pynixd: the temporary roots file is not open")

        for _ in range(RETRY_LIMIT):
            gc_lock = os.open(self.state / GC_LOCK_FILE, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
            try:
                if not self._hold_the_gc_lock(gc_lock) and not self._tell_the_collector(root):
                    time.sleep(RETRY_DELAY)
                    continue
                # Under the shared lock, so the collector cannot start
                # between this write and the read of the file that it makes.
                os.write(fd, root.encode() + b"\0")
                return
            finally:
                os.close(gc_lock)

        raise RuntimeError(f"pynixd: the collector did not take the temporary root {root!r}")

    def _hold_the_gc_lock(self, gc_lock: int) -> bool:
        """True when the collector is not running, and pynixd may write.

        The shared lock lasts until the caller closes `gc_lock`. The collector
        takes the same file for writing, so it waits for every reader.
        """
        try:
            fcntl.flock(gc_lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True

    def _create(self) -> None:
        """Make the roots file, and take the write lock that owns it."""
        self.dir.mkdir(parents=True, exist_ok=True)
        while True:
            # A file of this name is stale. The name holds the pid of this
            # process and a counter that never gives the same number twice.
            self.path.unlink(missing_ok=True)
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX)
            if os.fstat(fd).st_size == 0:
                self._fd = fd
                return
            # The collector deleted the file before the lock arrived, and it
            # wrote one byte to say so. Make another file.
            os.close(fd)

    def _tell_the_collector(self, root: str) -> bool:
        """Give the root to the collector over its socket.

        The collector holds the big lock while it runs, so it reads the
        `temproots` directory once and takes every later root over this
        socket. It answers one byte for each root that it took.

        False asks the caller to try again. The collector may have stopped
        between the refused lock and this call, and it may not have made the
        socket yet.
        """
        if self._socket is None:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                sock.connect(str(self.state / GC_SOCKET_PATH))
            except (FileNotFoundError, ConnectionRefusedError):
                sock.close()
                return False
            self._socket = sock

        try:
            self._socket.sendall(root.encode() + b"\n")
            ack = self._socket.recv(1)
        except OSError:
            ack = b""

        if ack == COLLECTOR_ACK:
            return True
        self._socket.close()
        self._socket = None
        return False

    def _close(self) -> None:
        if self._socket is not None:
            self._socket.close()
            self._socket = None
        if self._fd is not None:
            # Unlink before the close. A collector that already opened the
            # file reads the roots and keeps them, which is the safe answer.
            # A collector that opens it after this gets ENOENT and skips it,
            # which is also right, because the session is over.
            self.path.unlink(missing_ok=True)
            os.close(self._fd)  # This releases the write lock.
            self._fd = None
