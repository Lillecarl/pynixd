"""A store that refuses the capability probe is a store with fewer capabilities.

`DaemonStore._probe_systems` sends a `BuildDerivation` to find out what a
store can build. A Nix daemon refuses that operation to a client outside
`trusted-users`:

    you are not privileged to build input-addressed derivations

That is an answer about a capability, and `_send_probe` catches `BackendError`
to report it as one. The catch guarded nothing, because the nested read of
`WireResponse.logs` built a bare context and raised `DaemonProtocolError`
instead. `DaemonStore.start()` raised, so **pynixd could not start at all**
against such a daemon, and 149 tests of `tests/functional` failed at their
fixture in CI run 35147170272. Issues #46 and #47.

Nothing else covers this: the user of a developer machine is in
`trusted-users`, so every suite here gets an accepting daemon.
"""

from __future__ import annotations

from typing import Any, cast

import anyio
import pytest

from nix_daemon_protocol.constants import STDERR_ERROR, STDERR_LAST
from nix_daemon_protocol.io import BytesReader, BytesWriter
from pynixd.exceptions import BackendError
from pynixd.serde import BuildDerivationResponse, ReadContext
from pynixd.store.daemon import DaemonStore

REFUSAL = "you are not privileged to build input-addressed derivations"


def _refusal_bytes() -> bytes:
    """What the daemon writes in place of a `BuildDerivationResponse` body."""
    writer = BytesWriter()
    writer.write_uint64(STDERR_ERROR)
    writer.write_string("Error")  # type
    writer.write_uint64(0)  # level
    writer.write_string("Error")  # name
    writer.write_string(REFUSAL)
    writer.write_uint64(0)  # have_pos
    writer.write_uint64(0)  # traces
    writer.write_uint64(STDERR_LAST)
    return writer.get_bytes()


class _RefusingConnection:
    """The three attributes `ReadContext.from_conn` reads, and nothing else."""

    def __init__(self) -> None:
        self.r = BytesReader(_refusal_bytes(), identifier="refusing")
        self.version = 294
        self.standard_features: frozenset[str] = frozenset()


async def test_the_wire_raises_the_class_the_probe_catches() -> None:
    """The seam that broke, read exactly as `Connection.call` reads it.

    `DaemonStore.call` declares `raise_on_error=False`, so the call shape here
    is the production one down to that argument.
    """
    conn = cast("Any", _RefusingConnection())
    ctx = ReadContext.from_conn(conn, client=None, buffer_logs=True, raise_on_error=False)
    with pytest.raises(BackendError, match="not privileged"):
        await BuildDerivationResponse.from_reader(ctx)


class _RefusingStore:
    """Stands in for `DaemonStore`, holding what `_send_probe` reads."""

    def __init__(self) -> None:
        self.store_id = "refusing"
        self.asked: list[str] = []

    async def call(self, _request: object, **_kwargs: object) -> object:
        raise BackendError(REFUSAL)

    async def _send_probe(self, *args: Any, **kwargs: Any) -> tuple[str, bool, str]:
        """The real one, so the fan-out test measures the catch and not a double."""
        return await DaemonStore._send_probe(cast("Any", self), *args, **kwargs)


def _stand_in() -> Any:
    """A `DaemonStore` for the two methods under test, and for nothing else.

    The real one needs a spec, a pool and a connection. `_send_probe` and
    `_probe_systems` are called unbound against this, so the cast is what
    says they are.
    """
    return cast("Any", _RefusingStore())


async def test_a_refused_probe_is_an_answer_and_not_a_failure() -> None:
    name, ok, reason = await DaemonStore._send_probe(
        _stand_in(),
        "probe-system-x86_64-linux",
        "x86_64-linux",
        "",
        ["-c", "echo x > $out"],
    )
    assert name == "probe-system-x86_64-linux"
    assert ok is False
    assert reason == REFUSAL, "the reason travels, so a store with no system can say why"


async def test_the_system_fan_out_returns_empty_rather_than_raising() -> None:
    """`start()` must reach its end, so the store comes up with no systems."""
    systems = await DaemonStore._probe_systems(
        _stand_in(),
        {"x86_64-linux", "aarch64-linux"},
        anyio.CapacityLimiter(5),
    )
    assert systems == set()


async def test_a_store_with_no_system_says_why_at_warning_level(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A store that builds nothing is fatal for scheduling, so it is not a debug line.

    The scheduler answers `No compatible store for <system>`, which names the
    system the client wanted and not the store that could not take it. CI run
    35179826888 printed that message 35 times and nothing about its cause,
    because the per-probe reasons were at debug level. Issue #47.
    """
    with caplog.at_level("WARNING"):
        await DaemonStore._probe_systems(
            _stand_in(),
            {"x86_64-linux"},
            anyio.CapacityLimiter(5),
        )

    warnings = [r for r in caplog.records if "store_probed_no_system" in r.getMessage()]
    assert warnings, "a store that probed to nothing must say so above debug level"
    assert REFUSAL in warnings[0].getMessage(), "and it must carry the daemon's own reason"
