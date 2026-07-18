"""EnsurePath operation — WireRequest/WireResponse types."""

from __future__ import annotations

from typing import ClassVar

from .store_path import StorePath  # noqa: TC001
from .wire_ops import WireRequest, WireResponse


class EnsurePathResponse(WireResponse):
    """EnsurePath response — single uint64 value."""

    value: int


class EnsurePathRequest(WireRequest):
    """EnsurePath request — single StorePath on wire."""

    op: ClassVar[int] = 10
    response_type = EnsurePathResponse
    path: StorePath
