"""OptimiseStore operation — WireRequest/WireResponse types."""

from __future__ import annotations

from typing import ClassVar

from .wire_ops import WireRequest, WireResponse


class OptimiseStoreResponse(WireResponse):
    """OptimiseStore response — single uint64 value."""

    value: int


class OptimiseStoreRequest(WireRequest):
    """OptimiseStore request — no body fields, just op code."""

    op: ClassVar[int] = 34
    response_type = OptimiseStoreResponse
