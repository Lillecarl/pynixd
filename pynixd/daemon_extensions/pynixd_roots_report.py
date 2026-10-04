"""PynixdRootsReport operation — WireRequest/WireResponse types."""

from __future__ import annotations

from typing import ClassVar

from nix_daemon_protocol.wire_message import WireField, WireModel
from nix_daemon_protocol.wire_ops import WireRequest, WireResponse


class RootsReportRow(WireModel):
    """One root's storage: full and exclusive path counts and bytes."""

    label: str = ""
    full_paths: int = 0
    full_bytes: int = 0
    exclusive_paths: int = 0
    exclusive_bytes: int = 0


class PynixdRootsReportResponse(WireResponse):
    """PynixdRootsReport response — one row per root that attributes storage."""

    rows: list[RootsReportRow] = WireField(default_factory=list)


class PynixdRootsReportRequest(WireRequest):
    """PynixdRootsReport request — full and exclusive storage per root.

    The request carries nothing: the daemon walks its own roots and
    closes each one over the store graph. Slicing to a top-N happens on
    the client; the computation attributes every root exactly either
    way.

    New fields are a flag day for this operation: the body is read
    strictly, so a sender and a daemon from different revisions desync
    past the first unknown field. That is acceptable because the only
    sender is `pynixd roots`, which ships in the same package as the daemon.
    """

    op: ClassVar[int] = 110
    is_extension: ClassVar[bool] = True
    response_type = PynixdRootsReportResponse
