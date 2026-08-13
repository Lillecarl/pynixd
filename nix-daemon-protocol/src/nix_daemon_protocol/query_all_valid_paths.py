"""QueryAllValidPaths operation — WireRequest/WireResponse types."""

from __future__ import annotations

from typing import ClassVar

from .store_path import StorePath
from .wire_message import WireField
from .wire_ops import WireRequest, WireResponse


class QueryAllValidPathsResponse(WireResponse):
    """QueryAllValidPaths response — set of all valid StorePaths."""

    paths: set[StorePath] = WireField(default_factory=set)


class QueryAllValidPathsRequest(WireRequest):
    """QueryAllValidPaths request — no body fields, just op code."""

    op: ClassVar[int] = 23
    response_type = QueryAllValidPathsResponse
