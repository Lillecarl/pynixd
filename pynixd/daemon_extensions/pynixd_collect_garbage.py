"""PynixdCollectGarbage operation — WireRequest/WireResponse types."""

from __future__ import annotations

from typing import ClassVar

from nix_daemon_protocol.store_path import StorePath  # noqa: TC001
from nix_daemon_protocol.wire_message import WireField
from nix_daemon_protocol.wire_ops import WireRequest, WireResponse

from .protocol import PynixdGCAction  # noqa: TC001


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
