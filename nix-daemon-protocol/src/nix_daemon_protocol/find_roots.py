"""FindRoots operation — WireRequest/WireResponse types."""

from __future__ import annotations

from typing import ClassVar

from .wire_message import WireField, WireModel
from .wire_ops import WireRequest, WireResponse


class FindRootsEntry(WireModel):
    """A single root entry — link → target mapping."""

    link: str
    target: str


class FindRootsResponse(WireResponse):
    """FindRoots response — list of link/target entries."""

    roots: list[FindRootsEntry] = WireField(default_factory=list)


class FindRootsRequest(WireRequest):
    """FindRoots request — no body fields, just op code."""

    op: ClassVar[int] = 14
    response_type = FindRootsResponse
