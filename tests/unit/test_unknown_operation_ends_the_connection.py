"""An operation that pynixd does not know ends the connection.

Nothing reads the arguments of an unknown operation, so a loop that continues
reads the first argument as the next operation number. `nix-daemon` closes the
connection instead: `performOp` throws `invalid operation` at
`daemon.cc:1107`, before `logger->startWork()`, so `errorAllowed` at
`daemon.cc:1218` is false and the outer catch at `daemon.cc:1232` returns.
Issue #193.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from nix_daemon_protocol.operations import STANDARD_OPERATIONS
from pynixd.handlers._base import HANDLER_REGISTRY
from pynixd.proxy import DaemonProxy
from pynixd.serde.wire_ops import WIRE_REGISTRY

if TYPE_CHECKING:
    from collections.abc import Sequence

_UNKNOWN_OP = 4242
"""No operation of Nix carries this code, and none is reserved for it."""


class FakeReader:
    """A reader that answers a list of operation codes, then end of file."""

    def __init__(self, ops: Sequence[int]) -> None:
        self.remaining = list(ops)
        self.reads = 0

    async def read_uint64(self) -> int:
        self.reads += 1
        if not self.remaining:
            raise EOFError
        return self.remaining.pop(0)


class FakeProxy:
    """Enough of `DaemonProxy` for the loop to run."""

    def __init__(self, ops: Sequence[int]) -> None:
        self.r = FakeReader(ops)
        self.w = SimpleNamespace(drain=self._nothing)
        self.client = SimpleNamespace(flush=self._nothing)
        self.errors: list[str] = []
        self.dispatched: list[int] = []
        self._op_timing: dict[int, tuple[int, float]] = {}

    async def _nothing(self) -> None:
        return None

    async def send_error(self, text: str) -> None:
        self.errors.append(text)

    async def dispatch(self, op_num: int) -> None:
        self.dispatched.append(op_num)
        return None

    async def run(self) -> None:
        await DaemonProxy.op_loop(cast("DaemonProxy", self))


@pytest.mark.anyio
async def test_an_unknown_operation_ends_the_loop() -> None:
    """The error goes out, and the loop stops rather than read an argument."""
    proxy = FakeProxy([_UNKNOWN_OP, 1])

    await proxy.run()

    assert proxy.errors == [f"Unsupported operation: {_UNKNOWN_OP}"]
    assert proxy.dispatched == []
    # One read, and not two: the code of `IsValidPath` after it stays unread,
    # because that byte could equally be an argument of the unknown operation.
    assert proxy.r.reads == 1


@pytest.mark.anyio
async def test_a_known_operation_keeps_the_loop_going() -> None:
    """The close is for the unknown operation alone."""
    proxy = FakeProxy([1, 1])

    await proxy.run()

    assert proxy.errors == []
    assert proxy.dispatched == [1, 1]


def test_every_standard_operation_has_a_codec() -> None:
    """A gap in the manifest is what made the desync invisible."""
    known = set(WIRE_REGISTRY) | set(HANDLER_REGISTRY)
    missing = {op.code: op.name for op in STANDARD_OPERATIONS if op.code not in known}
    assert missing == {}


def test_the_manifest_leaves_out_only_what_no_client_sends() -> None:
    """The operations of Nix that this package answers with a close.

    Each entry names why it is out. `worker-protocol.hh` of Nix is the list to
    compare against.

    **A new operation of Nix comes with a feature name, and not with a new
    protocol number.** `worker-protocol.hh:105` states that rule, and 1.38 is
    the number that Nix 2.34, Nix 2.35 and the master branch all report. The
    last two entries below are therefore gated by a name that pynixd does not
    claim in the handshake. `tests/unit/test_protocol_features.py` holds the
    ledger of those names, and issue #162 holds the work.

    Both of those two belong to `builder-rpc-v0`, which is a derivation
    feature of dynamic derivations. Nix gives such a builder a restricted
    daemon socket and no output path in the environment, and the builder
    registers each output itself. It is not recursive Nix: the builder starts
    no build through that socket. `docs/notes/reentrancy.md` holds the
    detail, as Fact 9.
    """
    left_out = {
        8: "AddTextToStore, obsolete since protocol 1.25; the floor is 1.32",
        13: "SyncWithGC; no current RemoteStore sends it",
        18: "QueryDeriver, obsolete",
        22: "QueryDerivationOutputs, obsolete",
        28: "QueryDerivationOutputNames, obsolete",
        1000: "SubmitOutput; the `submit-output` feature of `builder-rpc-v0`",
        1001: "AddToStoreScanning; the `add-to-store-scanning` feature of `builder-rpc-v0`",
    }
    named = {op.code for op in STANDARD_OPERATIONS}
    assert not named & set(left_out)
    for code, reason in left_out.items():
        assert reason, code


def test_the_manifest_holds_the_two_substituter_operations() -> None:
    """Both were missing, and both are reachable. Issue #193."""
    names = {op.code: op.name for op in STANDARD_OPERATIONS}
    assert names[21] == "QuerySubstitutablePathInfo"
    assert names[30] == "QuerySubstitutablePathInfos"
