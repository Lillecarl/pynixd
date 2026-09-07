"""VerifyStore operation — WireRequest/WireResponse types."""

from __future__ import annotations

from typing import ClassVar

from .wire_ops import WireRequest, WireResponse


class VerifyStoreResponse(WireResponse):
    """VerifyStore response — single uint64 value."""

    value: int


class VerifyStoreRequest(WireRequest):
    """VerifyStore request — check_contents and repair flags."""

    op: ClassVar[int] = 35
    response_type = VerifyStoreResponse
    check_contents: int
    repair: int
