"""
Unix socket server that speaks the nix-daemon protocol.

Accepts connections on a Unix domain socket and spawns a DaemonProxy
for each client. Used for testing (avoids SSH) and local daemon mode.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import anyio
import anyio.to_thread
import structlog

from .config import ScheduleMode
from .proxy import DaemonProxy
from .trust import PeerRefusedError, authorise, peer_of
from .wire import UnixNixReader, UnixNixWriter

if TYPE_CHECKING:
    from pathlib import Path

    from .context import PynixdContext

log = structlog.get_logger(__name__)


async def start_unix_server(
    ctx: PynixdContext,
    socket_path: Path,
    schedule_mode: ScheduleMode | None = None,
) -> asyncio.Server:
    """Start a Unix socket server.

    Args:
        ctx: Shared application context
        socket_path: Path for the Unix domain socket
        schedule_mode: Scheduling mode for this listener

    Returns:
        The asyncio.Server instance.
    """

    async def handle_client(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        peer = peer_of(writer.get_extra_info("socket"))
        try:
            # A thread, because NSS may answer from the network.
            role, user = await anyio.to_thread.run_sync(authorise, peer, ctx.trust)
        except PeerRefusedError as ex:
            # nix-daemon closes the socket before the handshake, and the
            # client reads end-of-file.
            log.warning("unix_client_refused", pid=peer.pid, uid=peer.uid, reason=str(ex))
            writer.close()
            return
        log.info("unix_client_connected", pid=peer.pid, user=user, role=role.name)
        try:
            proxy = DaemonProxy(
                UnixNixReader(reader, identifier="client"),
                UnixNixWriter(writer, identifier="client"),
                ctx=ctx,
                role=role,
                username=user or "<unknown>",
                schedule_mode=schedule_mode or ScheduleMode.auto,
                transport="unix",
            )
            await proxy.run()
        except Exception:
            log.exception("unix_proxy_session_failed")
        finally:
            writer.close()

    # Clean up stale socket
    sock = anyio.Path(socket_path)
    if await sock.exists():
        await sock.unlink()

    server = await asyncio.start_unix_server(handle_client, path=str(socket_path), limit=2**18)
    await sock.chmod(0o666)
    log.info("unix_server_listening", socket_path=socket_path)
    return server
