"""Keep the standard 1.32+ operation manifest exact and complete."""

from __future__ import annotations

from nix_daemon_protocol.operations import STANDARD_OPERATIONS
from nix_daemon_protocol.wire_ops import WIRE_REGISTRY


def test_standard_operation_manifest_matches_registered_requests() -> None:
    """Fail on either a missing or undeclared standard request."""
    expected = {operation.code: (operation.name, operation.min_protocol) for operation in STANDARD_OPERATIONS}
    actual = {code: (request.name, request.min_protocol) for code, request in WIRE_REGISTRY.items()}

    assert actual == expected
