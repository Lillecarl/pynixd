"""QueryPathInfo operation — WireRequest/WireResponse types."""

from __future__ import annotations

from typing import ClassVar

from .path_info import UnkeyedValidPathInfo
from .store_path import StorePath
from .wire_message import WireField
from .wire_ops import WireRequest, WireResponse


class QueryPathInfoResponse(WireResponse):
    """QueryPathInfo response — info depends on valid flag."""

    valid: bool
    info: UnkeyedValidPathInfo | None = WireField(
        default=None,
        wire_depends_on=lambda self: self.valid,
    )


class QueryPathInfoRequest(WireRequest):
    """QueryPathInfo request — single StorePath on wire."""

    op: ClassVar[int] = 26
    response_type = QueryPathInfoResponse
    path: StorePath
