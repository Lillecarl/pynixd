"""QueryValidDerivers operation — WireRequest/WireResponse types."""

from __future__ import annotations

from typing import ClassVar

from .store_path import StorePath
from .wire_message import WireField
from .wire_ops import WireRequest, WireResponse


class QueryValidDeriversResponse(WireResponse):
    """QueryValidDerivers response — set of deriver StorePaths."""

    paths: set[StorePath] = WireField(default_factory=set)


class QueryValidDeriversRequest(WireRequest):
    """QueryValidDerivers request — single StorePath to look up derivers for."""

    op: ClassVar[int] = 33
    response_type = QueryValidDeriversResponse
    path: StorePath
