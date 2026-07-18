"""QueryRealisation operation — WireRequest/WireResponse types."""

from __future__ import annotations

from typing import ClassVar

from .drv_output import DrvOutput  # noqa: TC001
from .realisation import Realisation  # noqa: TC001
from .wire_message import WireField
from .wire_ops import WireRequest, WireResponse


class QueryRealisationResponse(WireResponse):
    """QueryRealisation response — list of Realisations (JSON strings on wire)."""

    realisations: list[Realisation] = WireField(default_factory=list)


class QueryRealisationRequest(WireRequest):
    """QueryRealisation request — DrvOutput string on wire (e.g. 'sha256:abc!out')."""

    op: ClassVar[int] = 43
    response_type = QueryRealisationResponse
    drv_output: DrvOutput
