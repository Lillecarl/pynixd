"""AddToStoreNar operation — WireRequest/WireResponse types.

The request has a structured header followed by framed NAR data.
Only the header fields are modeled here — NAR streaming is handled
by the transport layer, not the model.
"""

from __future__ import annotations

from typing import ClassVar

from .valid_path_info import ValidPathInfo  # noqa: TC001
from .wire_ops import WireRequest, WireResponse


class AddToStoreNarResponse(WireResponse):
    """AddToStoreNar response — empty body, just stderr/WireLogs."""


class AddToStoreNarRequest(WireRequest):
    """AddToStoreNar request header — NAR data follows but is not part of the model."""

    op: ClassVar[int] = 39
    response_type = AddToStoreNarResponse
    info: ValidPathInfo
    repair: int
    dont_check_sigs: int
