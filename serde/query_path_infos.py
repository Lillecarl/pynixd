"""QueryPathInfos operation — WireRequest/WireResponse types."""

from __future__ import annotations

from typing import ClassVar

from .store_path import StorePath  # noqa: TC001
from .valid_path_info import ValidPathInfo  # noqa: TC001
from .wire_message import WireField
from .wire_ops import WireRequest, WireResponse


class QueryPathInfosResponse(WireResponse):
    """QueryPathInfos response — list of ValidPathInfo objects."""

    infos: list[ValidPathInfo] = WireField(default_factory=list)


class QueryPathInfosRequest(WireRequest):
    """QueryPathInfos request — set of StorePaths to query."""

    op: ClassVar[int] = 103
    is_extension: ClassVar[bool] = True
    response_type = QueryPathInfosResponse
    paths: set[StorePath] = WireField(default_factory=set)
