"""
Nix daemon protocol client.

Connection is a concrete class that takes a pre-established reader/writer pair
and speaks the daemon protocol over it. Transport setup (subprocess, SSH,
Unix socket) is handled by Store types.

Lifecycle:
    r, w = <transport-specific setup>
    conn = Connection(r, w, "my-store")
    await conn.connect()          # handshake
    result = await conn.call(...)  # or typed methods
    await conn.close()
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

import anyio
import structlog

from . import wire
from .exceptions import InfrastructureError
from .protocol import get_extension_features
from .serde.context import ReadContext, WriteContext

if TYPE_CHECKING:
    from pathlib import Path
    from types import TracebackType

    from .serde.wire_message import WireModel
    from .serde.wire_ops import WireRequest
    from .wire import (
        NixReader,
        NixWriter,
    )

log = structlog.get_logger(__name__)


# ── Shared types ────────────────────────────────────────────────────


class ClientConn:
    """Client connection with serialized stderr writing.

    Multiple tasks can call send() concurrently; the internal lock
    ensures only one write+drain cycle runs at a time.
    No background drain task needed.

    Call flush() before writing the response to ensure all stderr is sent.
    """

    def __init__(self, w: NixWriter) -> None:
        """Wrap a writer for thread-safe stderr output to a client."""
        self.w = w
        self._write_lock = anyio.Lock()

    async def send(self, msg: WireModel) -> None:
        """Send a stderr message to the client. Safe to call from multiple tasks."""
        buf = wire.BytesWriter("client")
        await msg.to_writer(WriteContext(writer=buf, version=wire.PROTOCOL_VERSION))
        data = buf.get_bytes()
        if data:
            async with self._write_lock:
                self.w.write(data)
                await self.w.drain()

    async def send_raw(self, data: bytes) -> None:
        """Send raw bytes to the client. Safe to call from multiple tasks."""
        if data:
            async with self._write_lock:
                self.w.write(data)
                await self.w.drain()

    async def flush(self) -> None:
        """Wait until all pending writes are complete and OS buffer is drained."""
        async with self._write_lock:
            await self.w.drain()


# ── Connection ──────────────────────────────────────────────────────


class Connection:
    """Nix daemon protocol client over a pre-established transport.

    Takes a reader/writer pair and handles handshake, operation
    dispatch, stderr forwarding, and response decoding.
    """

    def __init__(
        self,
        r: NixReader,
        w: NixWriter,
        conn_id: str,
        store_path: Path | None = None,
    ) -> None:
        """Wrap a reader/writer pair as a daemon protocol connection.

        Args:
            r: Reader for incoming daemon traffic.
            w: Writer for outgoing daemon traffic.
            conn_id: Unique identifier for this connection (e.g. store name).
            store_path: Optional store root path for local Nix stores.
        """
        self.id: str = conn_id
        self.store_path: Path | None = store_path
        self.version: int = wire.PROTOCOL_VERSION
        self.nix_version: str = ""
        self.features: set[str] = set()
        self.r = r
        self.w = w
        self.connected: bool = False
        self.dirty: bool = False
        self.op_log: list[str] = []

    async def __aenter__(self) -> Connection:
        """Enter async context; no setup required."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        _exc_val: BaseException | None,
        _exc_tb: TracebackType | None,
    ) -> None:
        """Exit connection context. Marks as dirty if an exception occurred
        or if buffers are not empty.
        """
        if exc_type is not None:
            self.dirty = True

        if not self.dirty and (await self.r.is_dirty() or await self.w.is_dirty()):
            self.dirty = True

    async def connect(self) -> None:
        """Perform daemon protocol handshake."""
        await self.handshake(self.r, self.w)
        self.connected = True

    async def close(self) -> None:
        """Close the connection and release the underlying transport."""
        self.connected = False
        with contextlib.suppress(Exception):
            await self.w.close()

    async def call(
        self,
        request: WireRequest,
        client: ClientConn | None = None,
        suppress_last: bool = False,
        raise_on_error: bool = False,
    ) -> Any:
        """Send an operation on the established connection.

        Args:
            request: Request object with ClassVars for op and response_type
            client: If provided, stream stderr to this client via send()
            suppress_last: If True, consume STDERR_LAST
                but don't write it to client
            raise_on_error: If True, raise BackendError on stderr errors
        """
        if not self.connected:
            raise RuntimeError(f"Connection {self.id!r} not connected")

        self.op_log.append(type(request).__name__)

        await request.to_writer(WriteContext.from_conn(self))
        await self.w.drain()
        resp_cls = type(request).response_type
        return await resp_cls.from_reader(
            ReadContext.from_conn(
                self,
                client=client,
                buffer_logs=not suppress_last,
                raise_on_error=raise_on_error,
            ),
        )

    # ── Handshake ───────────────────────────────────────────────────

    async def handshake(
        self,
        r: NixReader,
        w: NixWriter,
    ) -> None:
        """Perform daemon protocol handshake (client side)."""
        self.w.write_uint64(wire.WORKER_MAGIC_1)
        await w.drain()

        magic = await self.r.read_uint64()
        if magic != wire.WORKER_MAGIC_2:
            raise ValueError(f"Bad magic: {magic:#x}")

        server_version = await self.r.read_uint64()
        self.version = min(wire.PROTOCOL_VERSION, server_version)
        if self.version < wire.MINIMUM_REMOTE_PROTOCOL:
            msg = (
                f"Store {self.id} negotiated protocol {wire.proto_str(self.version)}, "
                f"but we require >= {wire.proto_str(wire.MINIMUM_REMOTE_PROTOCOL)}"
            )
            log.error("protocol_version_too_low", store_id=self.id, error=msg)
            raise InfrastructureError(msg)
        log.debug(
            "daemon_protocol_negotiated",
            server_version=wire.proto_str(server_version),
            negotiated=wire.proto_str(self.version),
        )

        self.w.write_uint64(wire.PROTOCOL_VERSION)

        # Feature negotiation (1.38+) — before CPU/reserveSpace
        if self.version >= wire.proto(1, 38):
            self.w.write_string_set(get_extension_features())  # our features
            await w.drain()
            self.features = await self.r.read_string_set()
            log.debug("daemon_features", server_features=self.features)
        self.w.write_uint64(0)  # sendCpu
        self.w.write_uint64(0)  # reserveSpace
        await w.drain()

        # Server conditions these on clientVersion, not negotiated version
        if server_version >= wire.proto(1, 33):
            self.nix_version = await self.r.read_string()
            log.debug("daemon_nix_version", nix_version=self.nix_version)
            if server_version >= wire.proto(1, 35):
                await self.r.read_uint64()

        # Drain initial STDERR_LAST
        await self.r.drain_stderr()

    def __repr__(self) -> str:
        """String representation showing the connection identifier."""
        return f"Connection(id={self.id!r})"
