"""QueryPathFromHashPart operation — WireRequest/WireResponse types."""

from __future__ import annotations

from typing import ClassVar

from .store_path import StorePath
from .wire_ops import WireRequest, WireResponse


class QueryPathFromHashPartResponse(WireResponse):
    """QueryPathFromHashPart response — resolved StorePath."""

    value: StorePath


class QueryPathFromHashPartRequest(WireRequest):
    """QueryPathFromHashPart request — hash prefix string (NOT a StorePath)."""

    op: ClassVar[int] = 29
    response_type = QueryPathFromHashPartResponse
    path: str
