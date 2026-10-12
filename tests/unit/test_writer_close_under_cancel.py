"""A cancelled close still closes the transport. Issue #91.

A client hangup and a cancelled build both cancel through an anyio scope,
and anyio cancels every await in that scope, the cleanup's included. The
writer's close awaited the drain first, so the cancel skipped the close and
left the upstream connection open, and the daemon's work ran on.
"""

from __future__ import annotations

import asyncio
import socket

import anyio

from pynixd.connection import Connection
from pynixd.wire import UnixNixReader, UnixNixWriter


async def _stuck_writer() -> tuple[UnixNixWriter, asyncio.StreamReader, socket.socket]:
    """A writer whose peer never reads, filled until its drain blocks."""
    ours, theirs = socket.socketpair()
    reader, writer = await asyncio.open_unix_connection(sock=ours)
    nix_writer = UnixNixWriter(writer, identifier="test:stuck")
    nix_writer.write(b"\0" * (8 << 20))
    nix_writer.flush()
    return nix_writer, reader, theirs


async def test_a_cancelled_close_closes_the_transport() -> None:
    writer, _, peer = await _stuck_writer()
    try:
        with anyio.CancelScope() as scope:
            scope.cancel()
            await writer.close()
        assert writer.writer.is_closing()
    finally:
        peer.close()


async def test_a_cancelled_connection_close_reports_to_its_pool() -> None:
    writer, reader, peer = await _stuck_writer()
    conn = Connection(UnixNixReader(reader, identifier="test:stuck"), writer, conn_id="test:stuck")
    reported: list[Connection] = []
    conn.on_close = reported.append
    try:
        with anyio.CancelScope() as scope:
            scope.cancel()
            await conn.close()
        assert reported == [conn]
        assert writer.writer.is_closing()
    finally:
        peer.close()
