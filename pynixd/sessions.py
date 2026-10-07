"""
Established client sessions, and closing them on shutdown.

`Server.close` closes the listeners, which stops new clients, but the
handlers of established clients are tasks nobody holds: an in-flight
operation survives the shutdown sequence and its client waits on a dead
socket until systemd SIGKILLs the process. Issue #64.

`DaemonProxy.run` registers each session here on entry and unregisters
on exit, so this always names the sessions that exist right now.
`Server.close` calls `shutdown` after closing the listeners: every
in-flight operation is answered with an error first, so its client fails
fast and retries against the new daemon, and only then are the handlers
cancelled and waited on, with a deadline.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from .proxy import DaemonProxy

log = structlog.get_logger(__name__)

SHUTDOWN_ERROR = "pynixd: server is shutting down"
"""The text an in-flight operation carries when shutdown answers it."""

ERROR_TIMEOUT = 2.0
"""Give one error write this long. The client is blocked reading, so a slow
drain must not hold the shutdown sequence."""

SHUTDOWN_DEADLINE = 5.0
"""Wait this long for cancelled handlers to finish, then return anyway. A
handler that ignores cancellation is abandoned; the process exits next."""


class ClientSessions:
    """Every established client session, for the shutdown path."""

    def __init__(self) -> None:
        self._sessions: set[tuple[DaemonProxy, asyncio.Task[Any] | None]] = set()

    def __len__(self) -> int:
        return len(self._sessions)

    def by_transport(self) -> dict[str, int]:
        """Live sessions by transport. The state collector reads this."""
        counts: dict[str, int] = {}
        for proxy, _task in list(self._sessions):
            counts[proxy.transport] = counts.get(proxy.transport, 0) + 1
        return counts

    def track(self, proxy: DaemonProxy, task: asyncio.Task[Any] | None) -> None:
        """Remember *proxy* running as *task*. *task* is `None` when the
        session runs outside a task, and then shutdown answers it but
        cannot cancel it."""
        self._sessions.add((proxy, task))

    def untrack(self, proxy: DaemonProxy) -> None:
        """Forget *proxy*. Sessions unregister on exit, so a session that
        is gone is simply absent at shutdown."""
        for session in list(self._sessions):
            if session[0] is proxy:
                self._sessions.discard(session)

    async def shutdown(self, reason: str = SHUTDOWN_ERROR) -> None:
        """Answer every in-flight operation with *reason*, then close them.

        The error goes out before the cancel: the client is blocked reading
        its response, and a `STDERR_ERROR` fails it fast. Cancelling first
        would run the handler's `finally`, which closes the transport before
        the error goes out. The write races a response the handler may be
        encoding at this same moment; both outcomes fail the client fast,
        which is what matters -- no outcome leaves it waiting on a dead
        socket.
        """
        sessions = list(self._sessions)
        if not sessions:
            return
        log.info("server_closing_client_sessions", count=len(sessions))
        tasks: list[asyncio.Task[Any]] = []
        for proxy, task in sessions:
            if task is None or task.done():
                continue
            with contextlib.suppress(Exception):
                await asyncio.wait_for(proxy.send_error(reason), ERROR_TIMEOUT)
            if not task.done():
                task.cancel()
                tasks.append(task)
        if tasks:
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), SHUTDOWN_DEADLINE)
