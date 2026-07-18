"""PynixdCollectGarbage operation — WireRequest/WireResponse types."""

from __future__ import annotations

from typing import ClassVar

from .protocol import PynixdGCAction  # noqa: TC001
from .store_path import StorePath  # noqa: TC001
from .wire_message import WireField
from .wire_ops import WireRequest, WireResponse


class PynixdCollectGarbageResponse(WireResponse):
    """PynixdCollectGarbage response — store paths deleted + bytes freed."""

    store_paths: set[StorePath] = WireField(default_factory=set)
    bytes: int = 0


class PynixdCollectGarbageRequest(WireRequest):
    """PynixdCollectGarbage request — GC action (DRY_RUN or EXECUTE)."""

    op: ClassVar[int] = 101
    is_extension: ClassVar[bool] = True
    response_type = PynixdCollectGarbageResponse
    action: PynixdGCAction
