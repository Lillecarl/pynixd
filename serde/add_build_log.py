"""AddBuildLog operation — WireRequest/WireResponse types."""

from __future__ import annotations

from typing import ClassVar

from .store_path import StorePath  # noqa: TC001
from .wire_ops import WireRequest, WireResponse


class AddBuildLogResponse(WireResponse):
    """AddBuildLog response — single uint64 value."""

    value: int


class AddBuildLogRequest(WireRequest):
    """AddBuildLog request — single StorePath on wire."""

    op: ClassVar[int] = 45
    response_type = AddBuildLogResponse
    path: StorePath
