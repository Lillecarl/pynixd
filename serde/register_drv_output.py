"""RegisterDrvOutput operation — WireRequest/WireResponse types."""

from __future__ import annotations

from typing import ClassVar

from .realisation import Realisation  # noqa: TC001
from .wire_ops import WireRequest, WireResponse


class RegisterDrvOutputResponse(WireResponse):
    """RegisterDrvOutput response — empty body, just stderr/WireLogs."""


class RegisterDrvOutputRequest(WireRequest):
    """RegisterDrvOutput request — Realisation as JSON string on wire."""

    op: ClassVar[int] = 42
    response_type = RegisterDrvOutputResponse
    realisation: Realisation
