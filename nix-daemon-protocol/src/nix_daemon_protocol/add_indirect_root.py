"""AddIndirectRoot operation — WireRequest/WireResponse types."""

from __future__ import annotations

from typing import ClassVar

from .store_path import StorePath
from .wire_ops import WireRequest, WireResponse


class AddIndirectRootResponse(WireResponse):
    """AddIndirectRoot response — single uint64 value."""

    value: int


class AddIndirectRootRequest(WireRequest):
    """AddIndirectRoot request — single StorePath on wire."""

    op: ClassVar[int] = 12
    response_type = AddIndirectRootResponse
    path: StorePath
