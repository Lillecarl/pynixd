"""A Unix-socket proxy that breaks the link to a real daemon on request. Issue #93.

pynixd dials `listen`, and the proxy forwards to `upstream`. A test arms one
fault, and the next bytes the daemon sends trigger it. That is a response,
so the fault lands mid-operation on a connection that already finished its
handshake when the pool was warmed first.
"""

from __future__ import annotations

import asyncio
import contextlib
import enum
import socket
import struct
from typing import TYPE_CHECKING

import anyio

if TYPE_CHECKING:
    from pathlib import Path


class Fault(enum.Enum):
    CLOSE = "close"
    """Forward nothing more and close both sides: EOF mid-response."""
    RESET = "reset"
    """Close with SO_LINGER 0, so the peer reads ECONNRESET."""
    STALL = "stall"
    """Hold the response and keep the socket open, for ever."""
    CORRUPT = "corrupt"
    """Flip every byte of the next chunk."""
    DRIP = "drip"
    """From the next chunk on, forward one byte per `drip_delay`: alive, never silent long."""


class FaultProxy:
    def __init__(self, listen: Path, upstream: Path) -> None:
        self.listen = listen
        self.upstream = upstream
        self._armed: Fault | None = None
        self._links: set[tuple[asyncio.StreamWriter, asyncio.StreamWriter]] = set()
        self._tasks: set[asyncio.Task[None]] = set()
        self._server: asyncio.AbstractServer | None = None
        self.triggered = anyio.Event()
        self.drip_delay = 0.1

    async def start(self) -> None:
        self._server = await asyncio.start_unix_server(self._accept, path=str(self.listen))

    async def stop_listening(self) -> None:
        """Refuse new links: the socket goes away, as when the daemon dies."""
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        with contextlib.suppress(FileNotFoundError):
            self.listen.unlink()

    async def close(self) -> None:
        await self.stop_listening()
        self.cut_every_link()
        for task in list(self._tasks):
            task.cancel()
        for task in list(self._tasks):
            with contextlib.suppress(BaseException):
                await task

    def arm(self, fault: Fault) -> None:
        self.triggered = anyio.Event()
        self._armed = fault

    def cut_every_link(self) -> None:
        """Close every link while idle, as a daemon restart does."""
        for client_w, upstream_w in list(self._links):
            client_w.close()
            upstream_w.close()
        self._links.clear()

    @property
    def link_count(self) -> int:
        return len(self._links)

    async def _accept(self, client_r: asyncio.StreamReader, client_w: asyncio.StreamWriter) -> None:
        upstream_r, upstream_w = await asyncio.open_unix_connection(str(self.upstream))
        link = (client_w, upstream_w)
        self._links.add(link)
        down = asyncio.create_task(self._pump(upstream_r, client_w, link, from_daemon=True))
        up = asyncio.create_task(self._pump(client_r, upstream_w, link, from_daemon=False))
        self._tasks |= {down, up}
        for task in (down, up):
            task.add_done_callback(self._tasks.discard)

    async def _pump(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        link: tuple[asyncio.StreamWriter, asyncio.StreamWriter],
        *,
        from_daemon: bool,
    ) -> None:
        dripping = False
        try:
            while chunk := await reader.read(65536):
                fault = self._armed if from_daemon else None
                if fault is not None:
                    self._armed = None
                    self.triggered.set()
                    if fault is Fault.STALL:
                        await anyio.sleep_forever()
                    if fault is Fault.CORRUPT:
                        chunk = bytes(b ^ 0xFF for b in chunk)
                    elif fault is Fault.DRIP:
                        dripping = True
                    else:
                        self._break(link, reset=fault is Fault.RESET)
                        return
                if dripping:
                    for byte in chunk:
                        await anyio.sleep(self.drip_delay)
                        # pynixd cuts the link mid-drip; uvloop raises
                        # RuntimeError, not ConnectionError, for a write then.
                        if writer.is_closing():
                            return
                        writer.write(bytes([byte]))
                        await writer.drain()
                    continue
                writer.write(chunk)
                await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            for side in link:
                side.close()
            self._links.discard(link)

    def _break(self, link: tuple[asyncio.StreamWriter, asyncio.StreamWriter], *, reset: bool) -> None:
        client_w, upstream_w = link
        if reset:
            sock = client_w.get_extra_info("socket")
            if sock is not None:
                with contextlib.suppress(OSError):
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
            client_w.transport.abort()
        else:
            client_w.close()
        upstream_w.close()
        self._links.discard(link)
