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
    """PynixdCollectGarbage request — GC action (DRY_RUN or EXECUTE).

    `limit` narrows a pass and never widens it, riding a presence flag
    because the codec answers `None` only for string-ish scalars: an
    absent numeric has no wire shape. There is deliberately no
    target-usage override on the wire -- the daemon protocol has no
    floating-point type, and the store's `gc_target_usage` already sets
    policy. Both default off.

    New fields are a flag day for this operation: the body is read
    strictly, so a sender and a daemon from different revisions desync
    past the first unknown field. That is acceptable because the only
    sender is `pynixd gc`, which ships in the same package as the daemon.
    """

    op: ClassVar[int] = 101
    is_extension: ClassVar[bool] = True
    response_type = PynixdCollectGarbageResponse
    action: PynixdGCAction
    has_limit: bool = False
    """Whether `limit` bounds this pass. Off deletes the whole plan."""
    limit: int = 0
    """Delete at most this many paths, weight order first. `0` is unset;
    negative is refused. A dry-run reports the same head it would delete."""
