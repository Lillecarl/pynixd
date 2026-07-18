"""AddPermRoot operation — WireRequest/WireResponse types."""

from __future__ import annotations

from typing import ClassVar

from .wire_ops import WireRequest, WireResponse


class AddPermRootResponse(WireResponse):
    """AddPermRoot response — gc_root string."""

    gc_root: str


class AddPermRootRequest(WireRequest):
    """AddPermRoot request — two plain strings (store_path and gc_root)."""

    op: ClassVar[int] = 47
    response_type = AddPermRootResponse
    store_path: str
    gc_root: str
