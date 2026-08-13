"""AddBuildLog operation — WireRequest/WireResponse types."""

from __future__ import annotations

from typing import ClassVar

from .constants import proto
from .store_path import StorePath
from .wire_ops import WireRequest, WireResponse


class AddBuildLogResponse(WireResponse):
    """AddBuildLog response — single uint64 value."""

    value: int


class AddBuildLogRequest(WireRequest):
    """AddBuildLog request — single StorePath on wire."""

    op: ClassVar[int] = 45
    min_protocol: ClassVar[int] = proto(1, 32)
    response_type = AddBuildLogResponse
    path: StorePath
