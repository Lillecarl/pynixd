"""Optional structured logging for daemon protocol consumers.

The protocol package never configures logging. It uses structlog when the host
has it installed and otherwise falls back to the standard-library logger.
"""

from __future__ import annotations

import logging as stdlib_logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, Protocol

try:
    import structlog
except ImportError:  # structlog is intentionally an optional dependency.
    structlog = None

if TYPE_CHECKING:
    from .context import ReadContext


class ProtocolLogger(Protocol):
    """Minimal structured logging surface used by protocol decoding."""

    def exception(self, event: str, /, **fields: object) -> None: ...


class _StdlibLogger:
    """Adapt standard logging to the protocol's structured event shape."""

    def __init__(self, name: str) -> None:
        self._logger = stdlib_logging.getLogger(name)

    def exception(self, event: str, /, **fields: object) -> None:
        self._logger.exception(event, extra={"daemon_protocol": fields})


def get_logger(name: str) -> ProtocolLogger:
    """Return the host's structured logger without configuring it."""
    if structlog is not None:
        return structlog.get_logger(name)
    return _StdlibLogger(name)


_DEFAULT_LOGGER = get_logger("nix_daemon_protocol")
_DECODE_DEPTH: ContextVar[int] = ContextVar("nix_daemon_protocol_decode_depth", default=0)


def log_deserialization_failure(ctx: ReadContext, model_type: type, exc: Exception) -> None:
    """Report a failed outermost decode without including wire payload data."""
    reader = ctx.reader
    fields: dict[str, Any] = {
        "message_type": model_type.__name__,
        "protocol_version": ctx.version,
        "exception_type": type(exc).__name__,
    }
    operation = getattr(model_type, "op", None)
    if operation is not None:
        fields["operation"] = operation
    reader_id = getattr(reader, "identifier", None)
    if reader_id is not None:
        fields["reader_id"] = reader_id
    offset = getattr(reader, "tell", None)
    if callable(offset):
        fields["offset"] = offset()
    (ctx.logger or _DEFAULT_LOGGER).exception("daemon_deserialization_failed", **fields)


@contextmanager
def deserialization_scope(ctx: ReadContext, model_type: type):
    """Log only the outermost failure in a recursive decode operation."""
    depth = _DECODE_DEPTH.get()
    token = _DECODE_DEPTH.set(depth + 1)
    try:
        yield
    except Exception as exc:
        if depth == 0:
            log_deserialization_failure(ctx, model_type, exc)
        raise
    finally:
        _DECODE_DEPTH.reset(token)
