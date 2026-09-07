"""QueryClosureWithInfo operation — WireRequest/WireResponse types."""

from __future__ import annotations

from typing import ClassVar

from nix_daemon_protocol.store_path import StorePath  # noqa: TC001
from nix_daemon_protocol.valid_path_info import ValidPathInfo  # noqa: TC001
from nix_daemon_protocol.wire_message import WireField
from nix_daemon_protocol.wire_ops import WireRequest, WireResponse


class QueryClosureWithInfoResponse(WireResponse):
    """QueryClosureWithInfo response — list of ValidPathInfo objects."""

    infos: list[ValidPathInfo] = WireField(default_factory=list)


class QueryClosureWithInfoRequest(WireRequest):
    """QueryClosureWithInfo request — set of seed StorePaths."""

    op: ClassVar[int] = 105
    is_extension: ClassVar[bool] = True
    response_type = QueryClosureWithInfoResponse
    paths: set[StorePath] = WireField(default_factory=set)
