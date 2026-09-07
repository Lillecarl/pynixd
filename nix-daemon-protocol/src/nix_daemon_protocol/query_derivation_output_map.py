"""QueryDerivationOutputMap operation — WireRequest/WireResponse types."""

from __future__ import annotations

from typing import ClassVar

from .store_path import StorePath
from .wire_message import WireField
from .wire_ops import WireRequest, WireResponse


class QueryDerivationOutputMapResponse(WireResponse):
    """QueryDerivationOutputMap response — output name → StorePath mapping."""

    items: dict[str, StorePath] = WireField(default_factory=dict)


class QueryDerivationOutputMapRequest(WireRequest):
    """QueryDerivationOutputMap request — single derivation StorePath."""

    op: ClassVar[int] = 41
    response_type = QueryDerivationOutputMapResponse
    path: StorePath
