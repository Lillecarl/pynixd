"""QuerySubstitutablePathInfos operation — WireRequest/WireResponse types.

`QuerySubstitutablePaths` answers a set, and `Store::querySubstitutablePaths`
at `store-api.cc:507` skips each substituter whose `want-mass-query` is off. A
`file://` cache has it off, so that operation answers nothing for one. This
operation asks about a named set of paths, and it reads every substituter.
`Store::queryMissing` uses this one, and that is the reason the plan of Nix
holds a path that op 32 does not report.
"""

from __future__ import annotations

from typing import ClassVar

from .content_address import ContentAddress
from .store_path import StorePath
from .wire_message import WireField, WireModel
from .wire_ops import WireRequest, WireResponse


class SubstitutablePathInfo(WireModel):
    """What one substituter knows about one path.

    Wire order follows `daemon.cc:882`: the path, the deriver, the
    references, the download size and the NAR size.
    """

    path: StorePath
    deriver: StorePath | None = WireField(default=None)
    references: set[StorePath] = WireField(default_factory=set)
    download_size: int = WireField(default=0)
    nar_size: int = WireField(default=0)


class QuerySubstitutablePathInfosResponse(WireResponse):
    """QuerySubstitutablePathInfos response — one entry for each path found."""

    infos: list[SubstitutablePathInfo] = WireField(default_factory=list)


class QuerySubstitutablePathInfosRequest(WireRequest):
    """QuerySubstitutablePathInfos request — path to content address.

    A path with no content address carries the empty string, which is what
    `CommonProto::Serialise<std::optional<ContentAddress>>::write` writes at
    `common-protocol.cc:71`.
    """

    op: ClassVar[int] = 30
    response_type = QuerySubstitutablePathInfosResponse
    paths: dict[StorePath, ContentAddress] = WireField(default_factory=dict)
