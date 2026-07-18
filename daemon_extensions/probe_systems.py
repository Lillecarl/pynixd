"""ProbeSystems operation — WireRequest/WireResponse types."""

from __future__ import annotations

from typing import ClassVar

from nix_daemon_protocol.wire_message import WireField
from nix_daemon_protocol.wire_ops import WireRequest, WireResponse


class ProbeSystemsResponse(WireResponse):
    """ProbeSystems response — supported systems discovered by probing."""

    systems: set[str] = WireField(default_factory=set)


class ProbeSystemsRequest(WireRequest):
    """ProbeSystems request — candidate systems to probe."""

    op: ClassVar[int] = 108
    is_extension: ClassVar[bool] = True
    response_type = ProbeSystemsResponse
    systems: set[str] = WireField(default_factory=set)
