"""A nested wire field reads under the settings of the read that contains it.

`WireResponse.logs` is a `WireLogs`, so every response of the protocol reads
its stderr stream as a nested field. `WireLogs.from_reader` is the one reader
that acts on `error_factory` and on `raise_on_error`, and a nested read built
a context that carried neither.

The cost was measured in CI: pynixd's capability probe catches `BackendError`,
the daemon answered `you are not privileged to build input-addressed
derivations`, the nested read raised `DaemonProtocolError` instead, and 149
functional tests failed at their fixture because the store could not start.
"""

from __future__ import annotations

import pytest

from nix_daemon_protocol import PROTOCOL_VERSION
from nix_daemon_protocol.constants import STDERR_ERROR, STDERR_LAST
from nix_daemon_protocol.context import ReadContext
from nix_daemon_protocol.exceptions import DaemonProtocolError
from nix_daemon_protocol.io import BytesReader, BytesWriter
from nix_daemon_protocol.wire_ops import WireResponse


class _OwnError(Exception):
    """What a caller asks for in place of `DaemonProtocolError`."""


class _Response(WireResponse):
    """A response whose whole body is the inherited `logs` field."""


def _stderr_error(message: str) -> bytes:
    """One STDERR_ERROR record, as the daemon writes it."""
    writer = BytesWriter()
    writer.write_uint64(STDERR_ERROR)
    writer.write_string("Error")  # type
    writer.write_uint64(0)  # level
    writer.write_string("Error")  # name
    writer.write_string(message)
    writer.write_uint64(0)  # have_pos
    writer.write_uint64(0)  # traces
    writer.write_uint64(STDERR_LAST)
    return writer.get_bytes()


def _context(**kwargs: object) -> ReadContext:
    return ReadContext(
        reader=BytesReader(_stderr_error("you are not privileged"), identifier="nested"),
        version=PROTOCOL_VERSION,
        **kwargs,  # type: ignore[arg-type]
    )


async def test_error_factory_reaches_the_nested_log_stream() -> None:
    with pytest.raises(_OwnError, match="you are not privileged"):
        await _Response.from_reader(_context(error_factory=_OwnError))


async def test_the_default_factory_still_answers() -> None:
    with pytest.raises(DaemonProtocolError, match="you are not privileged"):
        await _Response.from_reader(_context())


async def test_raise_on_error_does_not_reach_the_nested_log_stream() -> None:
    """A nested stream raises whatever the caller asked not to raise.

    `DaemonStore.call` declares `raise_on_error=False` as its default, so
    honouring the flag here makes every daemon error a silent success.
    Measured: `_try_substitute_upstream` read a failed `EnsurePath` as a hit,
    and `nix build` waited 112 s for a substitution that never came. Issue
    #46 holds the question of what the default should be.
    """
    with pytest.raises(_OwnError):
        await _Response.from_reader(_context(error_factory=_OwnError, raise_on_error=False))


async def test_a_nested_read_does_not_send_to_the_log_sink() -> None:
    """`proxy.py` writes the buffered stream back, so a live copy would double it."""
    sent: list[object] = []

    class _Sink:
        async def send(self, message: object, /) -> None:
            sent.append(message)

    with pytest.raises(DaemonProtocolError):
        await _Response.from_reader(_context(log_sink=_Sink()))
    assert sent == []
