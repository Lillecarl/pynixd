"""BuildPathsWithResults operation — WireRequest/WireResponse types."""

from __future__ import annotations

from typing import ClassVar

from .constants import proto
from .derived_path import DerivedPath
from .keyed_build_result import KeyedBuildResult
from .wire_message import WireField
from .wire_ops import WireRequest, WireResponse


class BuildPathsWithResultsResponse(WireResponse):
    """BuildPathsWithResults response — list of KeyedBuildResults."""

    results: list[KeyedBuildResult] = WireField(default_factory=list)


class BuildPathsWithResultsRequest(WireRequest):
    """BuildPathsWithResults request — same wire format as BuildPaths.

    Wire fields: set[DerivedPath] then build_mode uint64.
    """

    op: ClassVar[int] = 46
    min_protocol: ClassVar[int] = proto(1, 34)
    forward: ClassVar[bool] = False
    response_type = BuildPathsWithResultsResponse
    derived_paths: set[DerivedPath] = WireField(default_factory=set)
    build_mode: int
