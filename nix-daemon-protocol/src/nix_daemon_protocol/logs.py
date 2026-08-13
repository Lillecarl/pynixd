"""WireModel types for the Nix daemon stderr/log stream.

The stderr stream uses a tagged-union wire format::

    [uint64 code][message body][uint64 code][message body]...[uint64 STDERR_LAST]

Each message type has a ``code`` field that is written on the wire
(``serialize=True``) but skipped during body reading (``deserialize=False``)
because ``WireLogs.from_reader`` dispatches on code before delegating to
the individual message's ``from_reader``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from .constants import (
    STDERR_ERROR,
    STDERR_LAST,
    STDERR_NEXT,
    STDERR_RESULT,
    STDERR_START_ACTIVITY,
    STDERR_STOP_ACTIVITY,
)
from .logging import deserialization_scope
from .protocol import FieldType
from .wire_message import WireField, WireModel

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from .context import ReadContext, WriteContext


# ── Helper: nested trace element ────────────────────────────────────


class TraceLine(WireModel):
    """A single trace entry inside ``LogError``."""

    pos: int = 0
    hint: str = ""


# ── Helper: tagged-union field inside activities ─────────────────────


class ActivityField(WireModel):
    """A single typed field in ``LogStartActivity`` or ``LogResult``.

    Wire format: ``uint64 type`` then either ``uint64 valint`` (if INT)
    or ``string valstr`` (if STRING).
    """

    type: FieldType = FieldType.INT
    valint: int | None = WireField(default=None, wire_depends_on=lambda self: self.type == FieldType.INT)
    valstr: str | None = WireField(default=None, wire_depends_on=lambda self: self.type == FieldType.STRING)


# ── Log message types ────────────────────────────────────────────────


class LogNext(WireModel):
    """STDERR_NEXT — a log line from the daemon."""

    code: int = WireField(default=STDERR_NEXT, serialize=True, deserialize=False)
    text: str = ""


class LogStartActivity(WireModel):
    """STDERR_START_ACTIVITY — begin a tracked activity."""

    code: int = WireField(default=STDERR_START_ACTIVITY, serialize=True, deserialize=False)
    act_id: int = 0
    level: int = 0
    type: int = 0
    text: str = ""
    fields: list[ActivityField] = WireField(default_factory=list)
    parent: int = 0


class LogStopActivity(WireModel):
    """STDERR_STOP_ACTIVITY — end a tracked activity."""

    code: int = WireField(default=STDERR_STOP_ACTIVITY, serialize=True, deserialize=False)
    act_id: int = 0


class LogResult(WireModel):
    """STDERR_RESULT — result data for an activity."""

    code: int = WireField(default=STDERR_RESULT, serialize=True, deserialize=False)
    act_id: int = 0
    result_type: int = 0
    fields: list[ActivityField] = WireField(default_factory=list)


class LogError(WireModel):
    """STDERR_ERROR — the daemon is reporting an error."""

    code: int = WireField(default=STDERR_ERROR, serialize=True, deserialize=False)
    type: str = ""
    level: int = 0
    name: str = ""
    msg: str = ""
    have_pos: int = 0
    traces: list[TraceLine] = WireField(default_factory=list)


# ── Tagged-union container ───────────────────────────────────────────

# Union of all log message types — used for type annotations
LogMessage = LogNext | LogStartActivity | LogStopActivity | LogResult | LogError

# Dispatch table: uint64 code → message class
_LOG_PARSERS: dict[int, type[WireModel]] = {
    STDERR_NEXT: LogNext,
    STDERR_START_ACTIVITY: LogStartActivity,
    STDERR_STOP_ACTIVITY: LogStopActivity,
    STDERR_RESULT: LogResult,
    STDERR_ERROR: LogError,
}


async def read_stream(ctx: ReadContext) -> AsyncIterator[LogMessage]:
    """Yield stderr/log messages until STDERR_LAST."""
    unknown_streak = 0
    while True:
        msg_type = await ctx.reader.read_uint64()

        if msg_type == STDERR_LAST:
            return

        parser = _LOG_PARSERS.get(msg_type)
        if parser is None:
            unknown_streak += 1
            if unknown_streak >= 3:
                raise ConnectionError(
                    f"Protocol desync: {unknown_streak} consecutive unknown stderr msg_types (last: 0x{msg_type:x})",
                )
            continue

        unknown_streak = 0
        msg = cast("LogMessage", await parser.from_reader(ctx))
        yield msg
        if isinstance(msg, LogError):
            return


async def drain(ctx: ReadContext, raise_on_error: bool = True) -> LogError | None:
    """Read and discard all stderr/log messages until STDERR_LAST."""
    last_error: LogError | None = None
    async for msg in read_stream(ctx):
        if isinstance(msg, LogError):
            last_error = msg
            if raise_on_error:
                raise ctx.error_factory(f"Backend error: {msg.msg}")
    return last_error


class WireLogs(WireModel):
    """Container for a complete stderr stream.

    ``from_reader`` reads a tagged-union stream (dispatching on ``code``),
    stopping at ``STDERR_LAST`` or after ``LogError``.

    ``to_writer`` writes each message (which includes its ``code``) followed
    by ``STDERR_LAST``.
    """

    messages: list[LogMessage] = WireField(default_factory=list)  # type: ignore[valid-type]

    def add(self, msg: LogMessage) -> None:  # type: ignore[valid-type]
        """Append a log message to the stream."""
        self.messages.append(msg)

    @classmethod
    async def from_reader(cls, ctx: ReadContext) -> WireLogs:
        """Read tagged-union stderr stream."""
        backend_error: Exception | None = None
        with deserialization_scope(ctx, cls):
            obj = cls.__new__(cls)
            object.__setattr__(obj, "__pydantic_fields_set__", {"messages"})
            object.__setattr__(obj, "__pydantic_extra__", None)
            object.__setattr__(obj, "__pydantic_private__", None)

            msgs: list[LogMessage] = []
            async for msg in read_stream(ctx):
                if ctx.log_sink:
                    await ctx.log_sink.send(msg)
                if ctx.buffer_logs:
                    msgs.append(msg)

                # STDERR_ERROR is a valid daemon response, not a decode failure.
                if isinstance(msg, LogError):
                    if ctx.raise_on_error:
                        backend_error = ctx.error_factory(f"Backend error: {msg.msg}")
                    break

            object.__setattr__(obj, "messages", msgs)

        if backend_error is not None:
            raise backend_error
        return obj

    async def to_writer(self, ctx: WriteContext) -> None:
        """Write each message body (including its code) then STDERR_LAST."""
        for msg in self.messages:
            await msg.to_writer(ctx)
        ctx.writer.write_uint64(STDERR_LAST)
