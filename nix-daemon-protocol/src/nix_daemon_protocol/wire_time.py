"""Time and TimeSpan — wire types for timestamps and durations."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from .wire_integer import WireUInt64


class Time(WireUInt64):
    """Unix timestamp in seconds on the wire. Exposes as datetime property."""

    def __new__(cls, value: int = 0, *, ts: int | None = None) -> Time:
        if ts is not None:
            if value and value != ts:
                raise ValueError("Time value and ts disagree")
            value = ts
        return super().__new__(cls, value)

    @property
    def ts(self) -> int:
        """Compatibility spelling for the raw Unix timestamp."""
        return self

    @property
    def datetime(self) -> datetime:
        return datetime.fromtimestamp(self, tz=UTC)


class TimeSpan(WireUInt64):
    """Timespan in seconds on the wire. Exposes as timedelta property."""

    def __new__(cls, value: int = 0, *, seconds: int | None = None) -> TimeSpan:
        if seconds is not None:
            if value and value != seconds:
                raise ValueError("TimeSpan value and seconds disagree")
            value = seconds
        return super().__new__(cls, value)

    @property
    def seconds(self) -> int:
        """Compatibility spelling for the raw duration in seconds."""
        return self

    @property
    def timedelta(self) -> timedelta:
        return timedelta(seconds=self)
