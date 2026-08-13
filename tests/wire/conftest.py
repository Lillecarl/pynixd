"""Fixtures and helpers for wire protocol tests."""

from __future__ import annotations

import pytest

from pynixd.constants import PROTOCOL_VERSION
from pynixd.serde.context import ReadContext, WriteContext
from pynixd.wire import BytesReader, BytesWriter


@pytest.fixture
def writer() -> BytesWriter:
    """Fresh BytesWriter per test."""
    return BytesWriter()


@pytest.fixture
def write_ctx(writer: BytesWriter) -> WriteContext:
    """WriteContext wrapping the fixture writer at PROTOCOL_VERSION."""
    return WriteContext(writer=writer, version=PROTOCOL_VERSION)


def read_ctx(data: bytes) -> ReadContext:
    """Create a ReadContext from raw bytes at PROTOCOL_VERSION."""
    return ReadContext(reader=BytesReader(data), version=PROTOCOL_VERSION)
