"""
Local Nix daemon store implementation.
"""

from __future__ import annotations

import asyncio
import atexit
import contextlib
import json
import os
import shlex
import signal
import time
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING

import anyio
import structlog

from ..config import LocalSocketStoreSpec, PynixdSettings
from ..connection import Connection
from ..monitor import DummyResourceMonitor, create_monitor
from ..store_path import StorePath
from ..wire import UnixNixReader, UnixNixWriter
from .daemon import DaemonStore

if TYPE_CHECKING:
    from ..drv_parser import Derivation
    from ..monitor import ResourceMonitor

log = structlog.get_logger(__name__)

# How many lines of daemon output to keep for a start-up error message. Enough
# to carry a Nix error and the lines around it, and bounded because a daemon
# that runs for a week must not grow a list for a week.
_RECENT_OUTPUT_LINES = 50


class LocalStore(DaemonStore):
    """Connects to a local nix-daemon via Unix socket.

    Manages a private daemon subprocess for isolated store paths. The root
    store instead connects to the host's system daemon: its store database is
    protected and cannot safely be served by an unprivileged private daemon.
    A managed socket is placed at
    ``<store_path>/<socket_path>`` and the daemon is told about it via
    NIX_DAEMON_SOCKET_PATH.

    If the configured socket_path is relative (the default), it is
    resolved relative to store_path. Absolute socket_paths are used as-is.
    """

    def __init__(self, spec: LocalSocketStoreSpec) -> None:
        """Configure local daemon paths, socket management, and monitor."""
        super().__init__(spec)

        socket_path = spec.socket_path or Path("nix/var/nix/daemon-socket/pynixd-nix")
        self.managed = self.store_path != Path("/")
        if not self.managed and not socket_path.is_absolute():
            self.socket_path = Path("/nix/var/nix/daemon-socket/socket")
        elif not socket_path.is_absolute():
            self.socket_path = self.store_path / socket_path
        else:
            self.socket_path = socket_path

        self.nix_bin = spec.nix_bin
        self.monitor_enabled = spec.monitor
        self.daemon_proc: asyncio.subprocess.Process | None = None
        self.daemon_ready: anyio.Event | None = None
        self._daemon_log_task: asyncio.Task | None = None
        # The last lines the daemon wrote, for the error paths of
        # `ensure_daemon`. Those used to call `daemon_proc.stderr.read()`, but
        # `_stream_daemon_output` holds a `readline()` on that same stream from
        # the moment the daemon starts, so the read raised "read() called while
        # another coroutine is already waiting for incoming data" and every
        # startup failure reported that instead of its own cause.
        self._recent_output: deque[str] = deque(maxlen=_RECENT_OUTPUT_LINES)
        self.nix_config = spec.nix_config
        self.extra_env = spec.extra_env or {}
        self.extra_args = spec.extra_args or []
        self.settings = spec.settings or PynixdSettings()

        # Register atexit handler to ensure a private daemon is killed even
        # if close() is never called. The system daemon is never ours to kill.
        if self.managed:
            atexit.register(self._atexit_kill_daemon)

        # Resource Monitoring
        self.monitor: ResourceMonitor | None = (
            create_monitor(self.gate, self.settings)
            if self.monitor_enabled
            else DummyResourceMonitor(self.gate, self.settings)
        )

    async def start(self, sync_paths: bool = True) -> None:
        """Spawn managed daemon and initialize the store."""
        await self.ensure_daemon()
        await super().start(sync_paths=sync_paths)

    async def ensure_daemon(self) -> None:
        """Ensure a daemon is reachable, spawning one if needed.

        Always spawns a private nix-daemon subprocess.  Uses an Event
        to coordinate concurrent callers.
        """
        if self.daemon_proc is not None:
            if self.daemon_ready is not None:
                await self.daemon_ready.wait()
            return

        if not self.managed:
            if not self.socket_path.exists():
                raise RuntimeError(f"System Nix daemon socket does not exist: {self.socket_path}")
            return

        self.daemon_ready = anyio.Event()

        if self.socket_path.exists():
            log.info("removing_stale_socket", socket_path=str(self.socket_path))
            self.socket_path.unlink()

        path = self.store_path or Path("/")
        socket_dir = self.socket_path.parent
        socket_dir.mkdir(parents=True, exist_ok=True)

        cmd = [
            self.nix_bin,
            "daemon",
            "--store",
            str(path),
            "--option",
            "build-dir",
            str(path / "nix" / "var" / "nix" / "builds"),
            "--log-format",
            "internal-json",
        ]
        cmd.extend(self.extra_args)

        log.info(
            "spawning_managed_daemon",
            nix_bin=self.nix_bin,
            store_path=str(path),
            socket_path=str(self.socket_path),
            builder_frontend="NIX_CONFIG" in self.extra_env,
            cmd=shlex.join(cmd),
        )
        env = os.environ.copy()
        env.update(self.extra_env)
        env["NIX_DAEMON_SOCKET_PATH"] = str(self.socket_path)
        env["NIX_DATA_DIR"] = str(self.store_path / "share")
        env["NIX_LOG_DIR"] = str(self.store_path / "var/log/nix")
        env["NIX_STATE_DIR"] = str(self.store_path / "var/nix")

        self.daemon_proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            start_new_session=True,
        )

        if self.daemon_proc.stdout:
            self._daemon_log_task = asyncio.create_task(
                self._stream_daemon_output(),
                name="daemon-log-forwarder",
            )

        # Wait for socket file to appear
        for _ in range(100):
            if self.socket_path.exists():
                break
            if self.daemon_proc.returncode is not None:
                stderr_output = self._recent_output_text()
                raise RuntimeError(
                    f"Managed daemon exited early with code {self.daemon_proc.returncode} "
                    f"(pid={self.daemon_proc.pid}): {stderr_output!r}",
                )
            await anyio.sleep(0.1)
        else:
            raise RuntimeError(
                f"Managed daemon did not create socket at {self.socket_path} within 10s (pid={self.daemon_proc.pid})",
            )

        daemon_ready = self.daemon_ready
        if daemon_ready is None:
            raise RuntimeError("daemon_ready event was not initialized")

        # Socket file exists but daemon may not be listening yet — probe
        for _attempt in range(100):
            if self.daemon_proc.returncode is not None:
                stderr_output = self._recent_output_text()
                raise RuntimeError(
                    f"Managed daemon exited with code {self.daemon_proc.returncode} "
                    f"(pid={self.daemon_proc.pid}): {stderr_output!r}",
                )
            if await self._probe_socket():
                log.info("daemon_socket_ready", socket_path=str(self.socket_path))
                await anyio.sleep(0.1)
                daemon_ready.set()
                return
            await anyio.sleep(0.05)

        stderr_output = self._recent_output_text()
        raise RuntimeError(
            f"Managed daemon socket not accepting connections "
            f"at {self.socket_path} within 5s (pid={self.daemon_proc.pid}): {stderr_output!r}",
        )

    def _recent_output_text(self) -> str:
        """The last lines the daemon wrote, for a start-up error message.

        Read from the buffer that `_stream_daemon_output` fills, and never from
        the stream. That forwarder holds a `readline()` on stdout and on stderr
        for as long as the daemon lives, so a second reader of either one gets
        `RuntimeError: read() called while another coroutine is already waiting
        for incoming data`. Each error path below used to do exactly that, so
        the only failure they could report was their own.
        """
        if not self._recent_output:
            return "(the daemon wrote nothing)"
        return "\n".join(self._recent_output)

    async def _stream_daemon_output(self) -> None:
        """Forward daemon stdout/stderr to structlog indefinitely."""
        if not self.daemon_proc or not self.daemon_proc.stdout or not self.daemon_proc.stderr:
            return

        async def _forward(stream: asyncio.StreamReader | None, label: str) -> None:
            if stream is None:
                return
            while True:
                line = await stream.readline()
                if not line:
                    break
                decoded = line.decode(errors="replace").rstrip()
                self._recent_output.append(f"{label}: {decoded}")
                if decoded.startswith("@nix "):
                    try:
                        data = json.loads(decoded[5:])
                    except json.JSONDecodeError:
                        log.info(f"daemon_{label}", msg=decoded)
                        continue
                    log.info(
                        f"daemon_{data.pop('action', label)}",
                        nix_id=data.pop("id", None),
                        nix_level=data.pop("level", None),
                        nix_parent=data.pop("parent", None),
                        nix_text=data.pop("text", ""),
                        nix_type=data.pop("type", None),
                        nix_fields=data.pop("fields", None),
                        **data,
                    )
                else:
                    log.info(f"daemon_{label}", msg=decoded)

        async with anyio.create_task_group() as tg:
            tg.start_soon(_forward, self.daemon_proc.stdout, "stdout")
            tg.start_soon(_forward, self.daemon_proc.stderr, "stderr")

    async def _probe_socket(self) -> bool:
        """Perform a full daemon handshake to verify a live Nix daemon.

        Connects to socket_path, does the protocol handshake, and cleanly
        closes. Returns True only if a real Nix daemon responded.
        """
        try:
            r, w = await asyncio.open_unix_connection(str(self.socket_path))
        except (ConnectionRefusedError, ConnectionResetError, FileNotFoundError, OSError):
            return False

        conn = Connection(
            UnixNixReader(r, identifier="probe"),
            UnixNixWriter(w, identifier="probe"),
            "probe",
            store_path=self.store_path,
        )
        try:
            await conn.connect()
        except (OSError, EOFError, ConnectionError):
            log.debug("probe_connection_failed", exc_info=True)
            with contextlib.suppress(Exception):
                await conn.close()
            return False
        await conn.close()
        return True

    async def create_conn(self) -> Connection:
        """Connect to the (managed or system) Nix daemon via Unix socket."""
        await self.ensure_daemon()
        conn_id = f"{self.store_id}-{self.conn_counter}"
        log.debug(
            "connecting_daemon_socket",
            socket_path=str(self.socket_path),
            conn_id=conn_id,
        )
        r, w = await asyncio.open_unix_connection(str(self.socket_path))
        conn = Connection(
            UnixNixReader(r, identifier=conn_id),
            UnixNixWriter(w, identifier=conn_id),
            conn_id,
            store_path=self.store_path,
        )
        await conn.connect()
        return conn

    async def read_derivation(self, drv_store_path: StorePath | str) -> Derivation | None:
        """Fast-path: read .drv file directly from the filesystem."""
        from ..drv_parser import parse_drv

        sp = StorePath(str(drv_store_path))
        drv_file = self.store_path / "nix" / "store" / str(sp)
        try:
            contents = drv_file.read_bytes()
        except (FileNotFoundError, OSError):
            return await super().read_derivation(drv_store_path)

        return parse_drv(contents.decode())

    async def close(self) -> None:
        """Close stores, stop monitor and terminate managed daemon if any."""
        await super().close()
        if self.monitor:
            await self.monitor.stop()
        if self._daemon_log_task and not self._daemon_log_task.done():
            self._daemon_log_task.cancel()
            with contextlib.suppress(BaseException):
                await self._daemon_log_task
        await self._kill_daemon()

    async def _kill_daemon(self) -> None:
        """Terminate the managed daemon process and all its children.

        The daemon is spawned with `start_new_session=True`, making it a
        session leader. Its PID equals the process group ID, so we can
        kill the entire group with `os.killpg`.
        """
        # Unregister atexit handler so we don't double-kill
        with contextlib.suppress(ValueError):
            atexit.unregister(self._atexit_kill_daemon)

        if self.daemon_proc is None:
            return
        pid = self.daemon_proc.pid
        log.info("terminating_daemon_process_group", pid=pid)

        # SIGTERM the entire process group (daemon + any children)
        with contextlib.suppress(ProcessLookupError):
            os.killpg(pid, signal.SIGTERM)

        try:
            with anyio.fail_after(5.0):
                await self.daemon_proc.wait()
        except TimeoutError:
            log.warning("daemon_sigterm_timeout_escalating_to_sigkill", pid=pid)
            with contextlib.suppress(ProcessLookupError):
                os.killpg(pid, signal.SIGKILL)

            # Final wait so the process is reaped
            with contextlib.suppress(TimeoutError, ProcessLookupError):
                with anyio.fail_after(2.0):
                    await self.daemon_proc.wait()

        self.daemon_proc = None
        self.daemon_ready = None

    def _atexit_kill_daemon(self) -> None:
        """Synchronous atexit handler to kill the daemon process group.
        This is called when the Python process exits without close() having
        been invoked (e.g., unhandled exception, os._exit, or interpreter crash).
        """
        proc = self.daemon_proc
        if proc is None:
            return

        pid = proc.pid
        log.info("atexit_killing_daemon_process_group", pid=pid)

        # Best-effort SIGTERM then SIGKILL synchronously
        with contextlib.suppress(ProcessLookupError):
            os.killpg(pid, signal.SIGTERM)

        # Very brief synchronous wait — atexit is synchronous, can't asyncio
        time.sleep(0.5)
        with contextlib.suppress(ProcessLookupError):
            os.killpg(pid, signal.SIGKILL)
