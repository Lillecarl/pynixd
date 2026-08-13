"""BuildDerivation operation — WireRequest/WireResponse types."""

from __future__ import annotations

from typing import ClassVar

from .basic_derivation import BasicDerivation
from .build_result import BuildResult
from .store_path import StorePath
from .wire_ops import WireRequest, WireResponse


class BuildDerivationResponse(WireResponse):
    """BuildDerivation response — a BuildResult wrapped in the response body."""

    result: BuildResult


class BuildDerivationRequest(WireRequest):
    """BuildDerivation request — wire format after the op code.

    Wire fields (in order):
      drv_path:   length-prefixed StorePath string
      derivation: BasicDerivation struct
      build_mode: uint64 (BuildMode int)
    """

    op: ClassVar[int] = 36
    response_type = BuildDerivationResponse
    forward: ClassVar[bool] = False

    drv_path: StorePath
    derivation: BasicDerivation
    build_mode: int
