"""PynixdState operation — WireRequest/WireResponse types."""

from __future__ import annotations

from typing import ClassVar

from nix_daemon_protocol.wire_message import WireField
from nix_daemon_protocol.wire_ops import WireRequest, WireResponse


class PynixdStateResponse(WireResponse):
    """PynixdState response — the collected sections as one JSON document."""

    version: int = 1
    payload: str = ""


class PynixdStateRequest(WireRequest):
    """PynixdState request — which sections, and whether to federate.

    `wants` names sections (`queue`, `stores`, `sessions`, `transfers`,
    `totals`); empty wants every section the daemon knows. Unknown names
    are ignored, so the wire shape never grows: a new section is a new
    name, not a new field, and an old daemon answers what it knows.

    `federated` asks the daemon to query each configured store's own
    state op and merge the answers. One level only: a federated query
    goes out with `federated` false, so no topology can loop.

    New fields are a flag day for this operation: the body is read
    strictly, so a sender and a daemon from different revisions desync
    past the first unknown field. That is acceptable because the only
    sender is `pynixd state`, which ships in the same package as the daemon.
    """

    op: ClassVar[int] = 111
    is_extension: ClassVar[bool] = True
    response_type = PynixdStateResponse

    version: int = 1
    wants: list[str] = WireField(default_factory=list)
    federated: bool = False
