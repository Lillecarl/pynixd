"""QueryReferrers operation — WireRequest/WireResponse types."""

from __future__ import annotations

from typing import ClassVar

from .store_path import StorePath
from .wire_message import WireField
from .wire_ops import WireRequest, WireResponse


class QueryReferrersResponse(WireResponse):
    """QueryReferrers response — set of referrer StorePaths."""

    paths: set[StorePath] = WireField(default_factory=set)


class QueryReferrersRequest(WireRequest):
    """QueryReferrers request — single StorePath on wire."""

    op: ClassVar[int] = 6
    response_type = QueryReferrersResponse
    path: StorePath
