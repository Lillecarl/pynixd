"""IsValidPath operation — WireRequest/WireResponse types."""

from __future__ import annotations

from typing import ClassVar

from .store_path import StorePath
from .wire_ops import WireRequest, WireResponse


class IsValidPathResponse(WireResponse):
    """IsValidPath response — valid bool as uint64 on wire."""

    valid: bool


class IsValidPathRequest(WireRequest):
    """IsValidPath request — single StorePath on wire."""

    op: ClassVar[int] = 1
    response_type = IsValidPathResponse
    path: StorePath
