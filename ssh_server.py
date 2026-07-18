"""
asyncssh SSH server that speaks the nix-daemon protocol.

Accepts SSH connections, authenticates, and spawns a DaemonProxy
for each `nix-daemon --stdio` exec request. Also provides an SFTP
subsystem restricted to PSI/cgroup monitoring files.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import asyncssh
import structlog

from . import wire
from .config import ScheduleMode
from .proxy import DaemonProxy
from .serde.auth import Role
from .sftp_server import PSIMonitorSFTPServer
from .wire import SSHNixReader, SSHNixWriter

if TYPE_CHECKING:
    from pathlib import Path

    from .context import PynixdContext

log = structlog.get_logger(__name__)


class _NixSSHServer(asyncssh.SSHServer):
    """Accept all authentication (development mode)."""

    def password_auth_supported(self) -> bool:
        return True

    def validate_password(self, username: str, password: str) -> bool:
        log.info("password_auth", username=username)
        return True

    def public_key_auth_supported(self) -> bool:
        return True

    def validate_public_key(self, username: str, key: asyncssh.SSHKey) -> bool:
        log.info(
            "pubkey_auth",
            username=username,
            key_fingerprint=key.get_fingerprint(),
        )
        return True


async def start_ssh_server(
    ctx: PynixdContext,
    host: str = "127.0.0.1",
    port: int = 0,
    host_key_path: Path | None = None,
    admin_users: set[str] | None = None,
    schedule_mode: ScheduleMode | None = None,
) -> asyncssh.SSHAcceptor:
    """Start the SSH server.

    Args:
        ctx: Shared application context
        host: Listen address
        port: Listen port (0 for random available port)
        host_key_path: Path to SSH host key
        admin_users: Set of usernames with admin privileges
        schedule_mode: Scheduling mode for this listener

    Returns:
        The asyncssh acceptor instance.
    """
    # Load or generate host key
    if host_key_path and host_key_path.exists():  # noqa: ASYNC240 — one-time startup check
        host_key: asyncssh.SSHKey = asyncssh.read_private_key(str(host_key_path))
        log.info("host_key_loaded_from_file", host_key_path=host_key_path)
    else:
        host_key = asyncssh.generate_private_key("ssh-rsa", key_size=4096)
        if host_key_path:
            host_key.write_private_key(str(host_key_path))
            log.info("host_key_generated", host_key_path=host_key_path)
        else:
            log.info("host_key_ephemeral_generated")

    async def handle_client(process: asyncssh.SSHServerProcess) -> None:
        cmd: str | None = process.command
        log.info("client_exec", cmd=cmd)
        if not cmd or ("nix-daemon" not in cmd and "nix daemon" not in cmd):
            process.stderr.write(b"pynixd: unsupported command\n")
            process.exit(1)
            return

        process.channel.set_write_buffer_limits(
            high=wire._SSH_WINDOW_SIZE,
            low=wire._SSH_WINDOW_SIZE // 4,
        )
        exit_code = 0
        username = str(process.get_extra_info("username", "unknown"))
        is_admin = admin_users and username in admin_users
        role = Role.ADMIN if is_admin else Role.USER
        try:
            proxy = DaemonProxy(
                SSHNixReader(process.stdin, identifier="client"),
                SSHNixWriter(process.stdout, identifier="client"),
                ctx=ctx,
                role=role,
                username=username,
                schedule_mode=schedule_mode or ScheduleMode.auto,
            )
            await proxy.run()
        except Exception:
            log.exception("proxy_session_failed")
            exit_code = 1
        finally:
            process.exit(exit_code)

    # Start the SSH server
    server = await asyncssh.listen(
        host,
        port,
        server_host_keys=[host_key],
        server_factory=_NixSSHServer,
        process_factory=handle_client,
        sftp_factory=PSIMonitorSFTPServer,
        encoding=None,
    )
    bound_port = server.get_port()
    log.info("ssh_server_listening", host=host, port=bound_port)
    return server
