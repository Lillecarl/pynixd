"""QueryMissing operation — WireRequest/WireResponse types."""

from __future__ import annotations

from typing import ClassVar

from .derived_path import DerivedPath
from .store_path import StorePath
from .wire_message import WireField
from .wire_ops import WireRequest, WireResponse


class QueryMissingResponse(WireResponse):
    """QueryMissing response — build/substitute/unknown classification."""

    will_build: set[StorePath] = WireField(default_factory=set)
    will_substitute: set[StorePath] = WireField(default_factory=set)
    unknown: set[StorePath] = WireField(default_factory=set)
    download_size: int
    nar_size: int


class QueryMissingRequest(WireRequest):
    """QueryMissing request — set of DerivedPaths to classify."""

    op: ClassVar[int] = 40
    response_type = QueryMissingResponse
    derived_paths: set[DerivedPath] = WireField(default_factory=set)
