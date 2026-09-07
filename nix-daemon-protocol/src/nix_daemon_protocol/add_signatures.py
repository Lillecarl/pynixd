"""AddSignatures operation — WireRequest/WireResponse types."""

from __future__ import annotations

from typing import ClassVar

from .signature import Signature
from .store_path import StorePath
from .wire_message import WireField
from .wire_ops import WireRequest, WireResponse


class AddSignaturesResponse(WireResponse):
    """AddSignatures response — single uint64 value."""

    value: int


class AddSignaturesRequest(WireRequest):
    """AddSignatures request — StorePath + set of Signatures."""

    op: ClassVar[int] = 37
    response_type = AddSignaturesResponse
    path: StorePath
    sigs: set[Signature] = WireField(default_factory=set)
