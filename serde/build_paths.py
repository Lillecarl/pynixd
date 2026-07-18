"""BuildPaths operation — WireRequest/WireResponse types."""

from __future__ import annotations

from typing import ClassVar

from .derived_path import DerivedPath  # noqa: TC001
from .wire_message import WireField
from .wire_ops import WireRequest, WireResponse


class BuildPathsResponse(WireResponse):
    """BuildPaths response — single uint64 value."""

    value: int


class BuildPathsRequest(WireRequest):
    """BuildPaths request — set of derived paths + build mode."""

    op: ClassVar[int] = 9
    response_type = BuildPathsResponse
    forward: ClassVar[bool] = False
    derived_paths: set[DerivedPath] = WireField(default_factory=set)
    build_mode: int
