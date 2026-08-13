"""pynixd-specific construction helpers for protocol codec contexts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from nix_daemon_protocol.context import ReadContext as ProtocolReadContext
from nix_daemon_protocol.context import WriteContext as ProtocolWriteContext

from ..exceptions import BackendError
from .auth import Role

if TYPE_CHECKING:
    from ..connection import ClientConn, Connection
    from ..proxy import DaemonProxy


@dataclass(frozen=True)
class RequestContext:
    """Daemon execution state passed to pynixd operation handlers."""

    proxy: DaemonProxy
    role: Role
    version: int
    username: str


class ReadContext(ProtocolReadContext):
    """Protocol read context with pynixd connection convenience constructors."""

    @classmethod
    def from_request(cls, ctx: RequestContext) -> ReadContext:
        return cls(reader=ctx.proxy.r, version=ctx.version, error_factory=BackendError)

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
            log_sink=client,
            buffer_logs=buffer_logs,
            raise_on_error=raise_on_error,
            error_factory=BackendError,
        )


class WriteContext(ProtocolWriteContext):
    """Protocol write context with pynixd connection convenience constructors."""

    @classmethod
    def from_request(cls, ctx: RequestContext) -> WriteContext:
        return cls(writer=ctx.proxy.w, version=ctx.version)

    @classmethod
    def from_conn(cls, conn: Connection) -> WriteContext:
        return cls(writer=conn.w, version=conn.version)

    @classmethod
    def from_proxy(cls, proxy: DaemonProxy) -> WriteContext:
        return cls(writer=proxy.w, version=proxy.version)
