"""AddTempRoots operation — WireRequest/WireResponse types."""

from __future__ import annotations

from typing import ClassVar

from .constants import proto
from .store_path import StorePath
from .wire_ops import WireRequest, WireResponse


class AddTempRootsResponse(WireResponse):
    """AddTempRoots response — single uint64 value (always 1 on success)."""

    value: int


class AddTempRootsRequest(WireRequest):
    """AddTempRoots request — a set of store paths, answered with 1.

    Only sent when the `addTempRoots` feature was negotiated (1.38+);
    older sessions use repeated AddTempRoot instead.
    """

    op: ClassVar[int] = 49
    min_protocol: ClassVar[int] = proto(1, 38)
    response_type = AddTempRootsResponse
    paths: set[StorePath]
