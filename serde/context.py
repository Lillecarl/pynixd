"""Execution context for daemon operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..connection import ClientConn, Connection
    from ..proxy import DaemonProxy
    from ..wire import NixReader, NixWriter
    from .auth import Role


@dataclass(frozen=True)
class RequestContext:
    """Context passed to operation handlers."""

    proxy: DaemonProxy
    role: Role
    version: int
    username: str


@dataclass(frozen=True)
class ReadContext:
    """Bundles the arguments needed to deserialize a response from the wire."""

    reader: NixReader
    version: int
    client: ClientConn | None = None
    buffer_logs: bool = True
    raise_on_error: bool = True

    @classmethod
    def from_request(cls, ctx: RequestContext) -> ReadContext:
        return cls(reader=ctx.proxy.r, version=ctx.version)

    @classmethod
    def from_conn(
        cls,
        conn: Connection,
        client: ClientConn | None = None,
        buffer_logs: bool | None = None,
        raise_on_error: bool = True,
    ) -> ReadContext:
        if buffer_logs is None:
            buffer_logs = client is None
        return cls(
            reader=conn.r,
            version=conn.version,
            client=client,
            buffer_logs=buffer_logs,
            raise_on_error=raise_on_error,
        )


@dataclass(frozen=True)
class WriteContext:
    """Bundles the arguments needed to serialize a request/response to the wire."""

    writer: NixWriter
    version: int

    @classmethod
    def from_request(cls, ctx: RequestContext) -> WriteContext:
        return cls(writer=ctx.proxy.w, version=ctx.version)

    @classmethod
    def from_conn(cls, conn: Connection) -> WriteContext:
        return cls(writer=conn.w, version=conn.version)

    @classmethod
    def from_proxy(cls, proxy: DaemonProxy) -> WriteContext:
        return cls(writer=proxy.w, version=proxy.version)
