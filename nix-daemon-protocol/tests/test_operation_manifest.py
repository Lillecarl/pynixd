"""Keep the standard 1.32+ operation manifest exact and complete."""

from __future__ import annotations

from nix_daemon_protocol.operations import STANDARD_OPERATIONS
from nix_daemon_protocol.wire_ops import WIRE_REGISTRY


def test_standard_operation_manifest_matches_registered_requests() -> None:
    """Fail on either a missing or undeclared standard request.

    `WIRE_REGISTRY` is one dictionary for the process, and `__init_subclass__`
    fills it. A consumer that defines its own request therefore adds to the
    registry of this package by importing its own modules. pynixd adds eight,
    and this test read all of them until `is_extension` filtered them out. It
    passed when this suite ran alone and failed when one process ran both, so
    the order of the suites decided the result.
    """
    expected = {operation.code: (operation.name, operation.min_protocol) for operation in STANDARD_OPERATIONS}
    actual = {
        code: (request.name, request.min_protocol)
        for code, request in WIRE_REGISTRY.items()
        if not request.is_extension
    }

    assert actual == expected
