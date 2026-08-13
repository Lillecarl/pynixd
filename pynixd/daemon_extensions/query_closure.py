"""QueryClosure operation — WireRequest/WireResponse types."""

from __future__ import annotations

from typing import ClassVar

from nix_daemon_protocol.store_path import StorePath  # noqa: TC001
from nix_daemon_protocol.wire_message import WireField
from nix_daemon_protocol.wire_ops import WireRequest, WireResponse


class QueryClosureResponse(WireResponse):
    """QueryClosure response — set of all StorePaths in the closure."""

    paths: set[StorePath] = WireField(default_factory=set)


class QueryClosureRequest(WireRequest):
    """QueryClosure request — set of seed StorePaths."""

    op: ClassVar[int] = 104
    is_extension: ClassVar[bool] = True
    response_type = QueryClosureResponse
    paths: set[StorePath] = WireField(default_factory=set)
