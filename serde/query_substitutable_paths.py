"""QuerySubstitutablePaths operation — WireRequest/WireResponse types."""

from __future__ import annotations

from typing import ClassVar

from .store_path import StorePath  # noqa: TC001
from .wire_message import WireField
from .wire_ops import WireRequest, WireResponse


class QuerySubstitutablePathsResponse(WireResponse):
    """QuerySubstitutablePaths response — set of substitutable StorePaths."""

    paths: set[StorePath] = WireField(default_factory=set)


class QuerySubstitutablePathsRequest(WireRequest):
    """QuerySubstitutablePaths request — set of StorePaths to check."""

    op: ClassVar[int] = 32
    response_type = QuerySubstitutablePathsResponse
    paths: set[StorePath] = WireField(default_factory=set)
