"""
SSH Store implementations for pynixd.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import anyio
import asyncssh
import structlog

from .. import wire
from ..config import (
    PynixdSettings,
    SSHSocketStoreSpec,
    SSHSubprocessStoreSpec,
)
from ..connection import Connection
from ..monitor import DummyResourceMonitor, GenericResourcePoller, ResourceMonitor
from ..wire import SSHNixReader, SSHNixWriter
from .daemon import DaemonStore

if TYPE_CHECKING:
    from ..psi import (
        CpuUtil,
        MemInfo,
    )
    from ..serde.ids import StoreId

log = structlog.get_logger(__name__)


class SSHStore(DaemonStore):
    """Shared SSH connection management with exponential backoff reconnection.

    Subclasses set host, port, username on __init__.
    """

    host: str
    port: int
    username: str | None
    client_keys: list[str | Path | asyncssh.SSHKey] | None
    conn: asyncssh.SSHClientConnection | None
    backoff: float
    max_backoff: float
    last_failure: float
    store_id: StoreId
    _bg_tasks: set[asyncio.Task[Any]]

    INITIAL_BACKOFF: float = 1.0
    MAX_BACKOFF: float = 60.0

    def init_ssh_state(
        self,
        *,
        monitor_enabled: bool = True,
        client_keys: list[str | Path | asyncssh.SSHKey] | None = None,
        settings: PynixdSettings | None = None,
    ) -> None:
        """Initialise SSH connection state, backoff, and resource monitor settings."""
        self.conn = None
        self.ssh_lock = anyio.Lock()
        self._bg_tasks = set()
        self.backoff = self.INITIAL_BACKOFF
        self.max_backoff = self.MAX_BACKOFF
        self.last_failure = 0.0
        self.monitor_enabled = monitor_enabled
        self.client_keys = client_keys
        self.settings = settings or PynixdSettings()
        self.monitor: ResourceMonitor | None = None

    async def start(self, sync_paths: bool = True) -> None:
        """Establish SSH connection and initialize the store."""
        await self.ensure_ssh()
        await super().start(sync_paths=sync_paths)

    def start_psi_polling(self, sftp: asyncssh.SFTPClient) -> None:
        """Start resource pressure polling over SFTP using the GenericResourcePoller."""

        """Start consolidated resource poller over SFTP."""
        if not self.monitor_enabled:
            # If monitoring is explicitly disabled, use dummy monitor with 0.0 load
            if self.monitor is None:
                self.monitor = DummyResourceMonitor(self.gate, self.settings)
                self.monitor.start()
            return

        async def sftp_read(path: str) -> str:
            async with sftp.open(path, "r") as f:
                attrs = await f.stat()
                if attrs.size:
                    return await f.read()
                chunks = []
                while True:
                    chunk = await f.read(65536)
                    if not chunk:
                        break
                    chunks.append(chunk)
                return "".join(chunks)

        async def sftp_exists(path: str) -> bool:
            try:
                await sftp.stat(path)
            except asyncssh.SFTPError:
                return False
            else:
                return True

        if self.monitor is None or isinstance(self.monitor, DummyResourceMonitor):
            if self.monitor:
                task = asyncio.create_task(self.monitor.stop())
                self._bg_tasks.add(task)
                task.add_done_callback(self._bg_tasks.discard)
            self.monitor = GenericResourcePoller(
                self.gate,
                self.settings,
                sftp_read,
                sftp_exists,
            )
            self.monitor.start()

    def stop_psi_polling(self) -> None:
        """Cancel the resource polling task."""

        """Cancel the resource polling task."""
        if self.monitor is not None:
            task = asyncio.create_task(self.monitor.stop())
            self._bg_tasks.add(task)
            task.add_done_callback(self._bg_tasks.discard)
            self.monitor = None

    @property
    def pressure(self) -> float | None:
        """System pressure score (0-100), or None if unavailable."""
        if self.monitor is None:
            return None

        if isinstance(self.monitor, DummyResourceMonitor):
            return 0.0

        if self.monitor.health.psi is None:
            return None

        # Stale check: 3x interval
        interval = getattr(self.monitor, "interval", 5.0)
        if time.monotonic() - self.monitor.health.timestamp > interval * 3:
            return None
        return self.monitor.health.psi.pressure_score()

    @property
    def meminfo(self) -> MemInfo | None:
        """System memory info, or None if unavailable."""
        return self.monitor.health.meminfo if self.monitor else None

    @property
    def cpu_util(self) -> CpuUtil | None:
        """CPU utilization from cgroupv2, or None if unavailable."""
        return self.monitor.health.cpu_util if self.monitor else None

    async def ensure_ssh(self) -> asyncssh.SSHClientConnection:
        """Establish or return the existing SSH connection, with backoff."""
        if self.conn is not None:
            return self.conn

        async with self.ssh_lock:
            # Re-check after acquiring lock (another task may have connected)
            if self.conn is not None:
                return self.conn

            # Respect backoff from previous failure
            now = time.monotonic()
            wait = self.last_failure + self.backoff - now
            if self.last_failure > 0 and wait > 0:
                log.info("ssh_backoff", store_id=self.store_id, backoff_seconds=wait)
                await anyio.sleep(wait)

            try:
                log.info(
                    "ssh_connecting",
                    username=self.username or "",
                    host=self.host,
                    port=self.port,
                )
                connect_kwargs: dict[str, Any] = {
                    "host": self.host,
                    "port": self.port,
                    "known_hosts": None,
                }
                if self.username is not None:
                    connect_kwargs["username"] = self.username
                if self.client_keys is not None:
                    connect_kwargs["client_keys"] = self.client_keys
                self.conn = await asyncssh.connect(**connect_kwargs)
                # Reset backoff on success
                self.backoff = self.INITIAL_BACKOFF
                self.last_failure = 0.0
                self.record_success()

                if self.monitor_enabled:
                    sftp = await self.conn.start_sftp_client()
                    self.start_psi_polling(sftp)
            except (asyncssh.misc.Error, OSError):
                self.last_failure = time.monotonic()
                self.backoff = min(self.backoff * 2, self.MAX_BACKOFF)
                self.record_failure()
                log.warning(
                    "ssh_connect_failed",
                    store_id=self.store_id,
                    next_retry_seconds=self.backoff,
                )
                raise
            else:
                return self.conn

    def invalidate_ssh(self) -> None:
        """Mark SSH connection as dead, triggering reconnect on next use."""

        """Mark SSH connection as dead so next ensure_ssh reconnects."""
        if self.conn is not None:
            with contextlib.suppress(Exception):
                self.conn.close()
            self.conn = None
        self._schedule_reconnect()

    async def close_ssh(self) -> None:
        """Stop resource polling and close the SSH connection."""

        self.stop_psi_polling()
        if self.conn is not None:
            self.conn.close()
            self.conn = None


class SSHSubprocessStore(SSHStore):
    """Persistent SSH connection, spawns nix-daemon --stdio channels.

    Used primarily for "fake Nix" stores like nixbuild.net that provide
    a nix-daemon protocol over stdin/stdout. For real Nix stores over SSH,
    SSHSocketStore (tunnelling to a Unix socket) is preferred.

    If store_path is set, runs ``nix daemon --store <path> --stdio``.
    Otherwise runs ``nix-daemon --stdio`` (default store, nixbuild.net compat).
    """

    def __init__(self, spec: SSHSubprocessStoreSpec) -> None:
        """Configure SSH subprocess with host, port, and client key settings."""
        super().__init__(spec)
        self.host = spec.host
        self.port = spec.port
        self.username = spec.username
        self.nix_bin = spec.nix_bin
        self.init_ssh_state(
            monitor_enabled=spec.monitor,
            client_keys=list(spec.client_keys) if spec.client_keys else None,
            settings=spec.settings,
        )
        self.ssh_processes: list[asyncssh.SSHClientProcess] = []

    async def create_conn(self) -> Connection:
        """Spawn a nix-daemon --stdio subprocess over the SSH connection."""
        ssh_conn = await self.ensure_ssh()
        conn_id = f"{self.store_id}-{self.conn_counter}"
        if self.store_path and self.store_path != Path("/"):
            cmd = f"{self.nix_bin} daemon --store {self.store_path} --stdio"
        elif self.nix_bin != "nix":
            cmd = f"{self.nix_bin} daemon --stdio"
        else:
            cmd = "nix-daemon --stdio"
        log.debug(
            "spawning_remote_daemon",
            cmd=cmd,
            conn_id=conn_id,
        )
        try:
            proc = await ssh_conn.create_process(cmd, encoding=None)
        except (asyncssh.misc.Error, OSError):
            self.invalidate_ssh()
            raise
        self.ssh_processes.append(proc)
        proc.channel.set_write_buffer_limits(
            high=wire._SSH_WINDOW_SIZE,
            low=wire._SSH_WINDOW_SIZE // 4,
        )

        conn = Connection(
            SSHNixReader(proc.stdout, identifier=conn_id),
            SSHNixWriter(proc.stdin, identifier=conn_id),
            conn_id,
        )
        await conn.connect()
        return conn

    async def close(self) -> None:
        """Close daemon store, terminate subprocess channels, and close SSH connection."""
        await super().close()
        for proc in self.ssh_processes:
            with contextlib.suppress(Exception):
                proc.terminate()
            proc.close()
        self.ssh_processes.clear()
        await self.close_ssh()


_DAEMON_SOCKET_PATH = Path("/nix/var/nix/daemon-socket/socket")


class SSHSocketStore(SSHStore):
    """Persistent SSH connection, tunnels to remote Unix socket."""

    def __init__(self, spec: SSHSocketStoreSpec) -> None:
        """Configure SSH socket tunnel store with host, port, and remote socket path."""
        super().__init__(spec)
        self.host = spec.host
        self.port = spec.port
        self.username = spec.username
        self.socket_path = spec.socket_path
        self.init_ssh_state(
            monitor_enabled=spec.monitor,
            client_keys=list(spec.client_keys) if spec.client_keys else None,
            settings=spec.settings,
        )

    async def create_conn(self) -> Connection:
        """Open a Unix socket tunnel over the SSH connection."""
        ssh_conn = await self.ensure_ssh()
        conn_id = f"{self.store_id}-{self.conn_counter}"
        log.debug(
            "tunneling_to_socket",
            socket_path=str(self.socket_path),
            conn_id=conn_id,
        )
        try:
            r, w = await ssh_conn.open_unix_connection(str(self.socket_path))
        except (asyncssh.misc.Error, OSError):
            self.invalidate_ssh()
            raise
        conn = Connection(
            SSHNixReader(r, identifier=conn_id),
            SSHNixWriter(w, identifier=conn_id),
            conn_id,
        )
        await conn.connect()
        return conn

    async def close(self) -> None:
        """Close daemon store and close the SSH connection."""
        await super().close()
        await self.close_ssh()
