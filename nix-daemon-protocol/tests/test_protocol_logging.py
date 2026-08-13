"""Tests for protocol-local deserialization failure logging."""

from __future__ import annotations

from enum import IntEnum

import pytest

from nix_daemon_protocol import PROTOCOL_VERSION
from nix_daemon_protocol import logging as protocol_logging
from nix_daemon_protocol.context import ReadContext
from nix_daemon_protocol.io import BytesReader, BytesWriter
from nix_daemon_protocol.wire_message import WireModel


class _RecordingLogger:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def exception(self, event: str, /, **fields: object) -> None:
        self.events.append((event, fields))


class _Code(IntEnum):
    OK = 0


class _Inner(WireModel):
    code: _Code


class _Envelope(WireModel):
    inner: _Inner


def test_stdlib_logger_is_used_without_structlog(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The optional structured dependency is not required for diagnostics."""
    monkeypatch.setattr(protocol_logging, "structlog", None)
    logger = protocol_logging.get_logger("nix_daemon_protocol.test")

    with caplog.at_level("ERROR"):
        try:
            raise ValueError("bad wire value")
        except ValueError:
            logger.exception("daemon_deserialization_failed", message_type="TestMessage")

    assert caplog.records[-1].message == "daemon_deserialization_failed"
    assert caplog.records[-1].__dict__["daemon_protocol"] == {"message_type": "TestMessage"}


async def test_nested_deserialization_failure_is_logged_once() -> None:
    """The outer message reports one failure even when a nested field rejects data."""
    writer = BytesWriter()
    writer.write_uint64(99)
    logger = _RecordingLogger()

    with pytest.raises(ValueError, match="99"):
        await _Envelope.from_reader(
            ReadContext(
                reader=BytesReader(writer.get_bytes(), identifier="invalid-envelope"),
                version=PROTOCOL_VERSION,
                logger=logger,
            ),
        )

    assert logger.events == [
        (
            "daemon_deserialization_failed",
            {
                "message_type": "_Envelope",
                "protocol_version": PROTOCOL_VERSION,
                "exception_type": "ValueError",
                "reader_id": "invalid-envelope",
                "offset": 8,
            },
        ),
    ]
