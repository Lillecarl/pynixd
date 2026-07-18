"""
Reverse store — builder-initiated reverse-SSH connection to the controller.

Each ReverseStore wraps a reverse SSH connection (SSHClientConnection from the
controller's perspective).  ``create_conn()`` opens new ``nix-daemon --stdio``
processes on the builder over this connection, providing full connection pooling
that follows the same pattern as ``SSHSubprocessStore``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from .. import wire
from ..connection import Connection
from ..wire import SSHNixReader, SSHNixWriter
from .daemon import ProbeState
from .ssh import SSHStore

if TYPE_CHECKING:
    import asyncssh

    from ..config import ReverseStoreSpec

log = structlog.get_logger(__name__)


class ReverseStore(SSHStore):
    """A store where the builder connected TO us via reverse SSH.

    The controller holds an ``SSHClientConnection`` (obtained via
    :func:`asyncssh.listen_reverse`) and uses it to spawn
    ``nix-daemon --stdio`` processes on the builder.
    """

    def __init__(self, spec: ReverseStoreSpec, ssh_conn: asyncssh.SSHClientConnection) -> None:
        """Wrap an existing reverse SSH connection for daemon operations on the builder."""
        super().__init__(spec)
        self._ssh_conn: asyncssh.SSHClientConnection = ssh_conn
        self.nix_bin = spec.nix_bin

    async def create_conn(self) -> Connection:
        """Spawn nix-daemon --stdio on the builder over the reverse SSH connection."""
        conn_id = f"{self.store_id}-{self.conn_counter}"
        cmd = "nix-daemon --stdio" if self.nix_bin == "nix" else f"{self.nix_bin} daemon --stdio"
        log.debug(
            "reverse_spawning_remote_daemon",
            cmd=cmd,
            conn_id=conn_id,
            store_id=self.store_id,
        )
        proc = await self._ssh_conn.create_process(cmd, encoding=None)
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

    async def probe(self) -> None:
        """Mark as probed immediately — metadata comes from the registration handshake."""
        if self.probe_state == ProbeState.PROBED:
            return
        self.probe_state = ProbeState.PROBED
        self._probe_event.set()
