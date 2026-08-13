"""Transport-neutral contexts used by daemon message codecs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from .exceptions import DaemonProtocolError

if TYPE_CHECKING:
    from collections.abc import Callable

    from .io import NixReader, NixWriter
    from .logging import ProtocolLogger


class LogSink(Protocol):
    """Optional real-time receiver for decoded daemon log records."""

    async def send(self, message: object, /) -> None: ...


@dataclass(frozen=True)
class ReadContext:
    """Bundles the arguments needed to deserialize a response from the wire."""

    reader: NixReader
    version: int
    log_sink: LogSink | None = None
    buffer_logs: bool = True
    raise_on_error: bool = True
    error_factory: Callable[[str], Exception] = DaemonProtocolError
    logger: ProtocolLogger | None = None


@dataclass(frozen=True)
class WriteContext:
    """Bundles the arguments needed to serialize a request/response to the wire."""

    writer: NixWriter
    version: int
