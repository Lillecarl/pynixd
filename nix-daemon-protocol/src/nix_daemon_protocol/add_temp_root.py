"""AddTempRoot operation — WireRequest/WireResponse types."""

from __future__ import annotations

from typing import ClassVar

from .store_path import StorePath
from .wire_ops import WireRequest, WireResponse


class AddTempRootResponse(WireResponse):
    """AddTempRoot response — single uint64 value (always 1 on success)."""

    value: int


class AddTempRootRequest(WireRequest):
    """AddTempRoot request — single StorePath on wire."""

    op: ClassVar[int] = 11
    response_type = AddTempRootResponse
    path: StorePath
