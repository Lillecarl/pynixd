"""CollectGarbage operation — WireRequest/WireResponse types."""

from __future__ import annotations

from typing import ClassVar

from .protocol import GCAction  # noqa: TC001
from .store_path import StorePath  # noqa: TC001
from .wire_message import WireField
from .wire_ops import WireRequest, WireResponse


class CollectGarbageResponse(WireResponse):
    """CollectGarbage response — paths deleted + bytes freed."""

    paths_deleted: set[StorePath] = WireField(default_factory=set)
    bytes_freed: int
    obsolete: int


class CollectGarbageRequest(WireRequest):
    """CollectGarbage request — action, paths, and GC options."""

    op: ClassVar[int] = 20
    response_type = CollectGarbageResponse
    action: GCAction
    paths_to_delete: set[StorePath] = WireField(default_factory=set)
    ignore_liveness: int
    max_freed: int
    obsolete1: int
    obsolete2: int
    obsolete3: int
