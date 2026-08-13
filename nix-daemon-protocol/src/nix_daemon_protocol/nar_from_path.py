"""NarFromPath operation — WireRequest type (streaming response, not modelable)."""

from __future__ import annotations

from typing import ClassVar

from .store_path import StorePath
from .wire_ops import WireRequest, WireResponse


class NarFromPathResponse(WireResponse):
    """NarFromPath response — empty (raw NAR bytes are streamed separately)."""


class NarFromPathRequest(WireRequest):
    """NarFromPath request — single StorePath. Response is raw NAR bytes."""

    op: ClassVar[int] = 38
    response_type = NarFromPathResponse
    path: StorePath
