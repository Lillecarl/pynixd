"""A codec context that faces a peer carries that peer's negotiated features.

**The protocol number stopped at 1.38, and a feature decides the shape of a
field.** `WireField` takes `needs_features` and `unless_features`, and it
reads `ReadContext.features` and `WriteContext.features`. A context built with
no set writes the Nix 2.34 shape, whatever the peer agreed to.

**A proxy has two negotiated sets, and they are not the same.** The client
handshake gives `proxy.standard_features`; each backend handshake gives
`conn.standard_features`. A context built from the wrong one puts the shape of
one peer on the wire of the other, and the wire holds no marker that says
which shape it carries, so the other side decodes a wrong value rather than
raising. Issue #162.

This is the machine-checkable half of that rule.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from pynixd.connection import ClientConn
from pynixd.serde import LogNext
from pynixd.wire import BytesWriter

_SOURCE = Path(__file__).resolve().parent.parent.parent / "pynixd"

CONTEXT_NAMES = {"ReadContext", "WriteContext"}

NO_PEER: dict[str, str] = {
    "build_queue.py": (
        "It writes into the log buffer of a build, and not to a peer. The "
        "buffer is replayed to each subscriber, so the shape has to be the "
        "one every client can read, which is the one with no feature. A log "
        "message carries no feature-gated field today."
    ),
    "wire.py": (
        "It reads a log stream out of a buffer that pynixd itself wrote. The "
        "two ends are the same process, and no handshake stands between them."
    ),
    "add_multiple_to_store.py": (
        "One of its two contexts reads a `ValidPathInfo` out of the framing "
        "of `AddMultipleToStore` at a fixed version 1. That framing is not "
        "the connection, and no feature reaches it."
    ),
}
"""Each module that may build a context with no feature set, and why.

Add an entry only for a context that faces no peer. A context on a connection
belongs to the set that the connection negotiated.
"""


def _context_calls(path: Path) -> list[ast.Call]:
    """Every direct `ReadContext(...)` or `WriteContext(...)` call in *path*.

    A `from_request` / `from_conn` / `from_proxy` call is an attribute call
    and does not appear here. Those constructors take the set themselves, in
    `pynixd/serde/context.py`.
    """
    tree = ast.parse(path.read_text())
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in CONTEXT_NAMES
    ]


def _modules_building_a_context() -> list[Path]:
    return sorted(path for path in _SOURCE.rglob("*.py") if _context_calls(path))


def test_a_context_that_faces_a_peer_names_its_features() -> None:
    bare: list[str] = []
    for path in _modules_building_a_context():
        if path.name in NO_PEER:
            continue
        for call in _context_calls(path):
            if not any(keyword.arg == "features" for keyword in call.keywords):
                bare.append(f"{path.name}:{call.lineno}")

    assert bare == [], (
        f"these contexts carry no feature set: {sorted(bare)}. Pass "
        f"`features=` from the handshake of the peer they face, or use a "
        f"`from_request` / `from_conn` / `from_proxy` constructor. Issue #162."
    )


def test_the_reason_of_each_module_with_no_peer_is_written_down() -> None:
    """An entry with no reason is a hole that reads as a decision."""
    names = {path.name for path in _modules_building_a_context()}
    for name, reason in NO_PEER.items():
        assert name in names, f"{name} builds no context any more; drop the entry"
        assert len(reason) > 40, name


def test_the_two_sets_of_a_proxy_stay_apart() -> None:
    """`from_request` reads the client set, and `from_conn` the backend one.

    A single set would be right until the first backend that offers a
    different one, and then wrong with no error on the wire.
    """
    source = (_SOURCE / "serde" / "context.py").read_text()

    assert "features=ctx.proxy.standard_features" in source
    assert "features=conn.standard_features" in source
    assert "features=proxy.standard_features" in source


@pytest.mark.anyio
async def test_a_client_connection_can_send_before_the_handshake() -> None:
    """`ClientConn.send` builds a context, so it needs a set of its own.

    It had none. The AST guard above asks every context for `features=`, and
    satisfying it here with `self.standard_features` named an attribute that
    `ClientConn` did not carry. Every log line to a client then raised
    `AttributeError`, and the client read "Nix daemon disconnected
    unexpectedly". The `ca` suite against Nix 2.35 found it; no unit test
    called this method.

    The set is empty until `DaemonProxy.handshake` fills it in, which is the
    right answer before a handshake: the two sides have agreed on nothing.
    Issue #162.
    """
    client = ClientConn(BytesWriter("client"))

    assert client.standard_features == frozenset()
    await client.send(LogNext(text="hello\n"))
