"""SignPathInfo operation — WireRequest/WireResponse types."""

from __future__ import annotations

from typing import ClassVar

from .valid_path_info import ValidPathInfo  # noqa: TC001
from .wire_ops import WireRequest, WireResponse


class SignPathInfoResponse(WireResponse):
    """SignPathInfo response — signed ValidPathInfo."""

    info: ValidPathInfo


class SignPathInfoRequest(WireRequest):
    """SignPathInfo request — ValidPathInfo to sign."""

    op: ClassVar[int] = 107
    is_extension: ClassVar[bool] = True
    response_type = SignPathInfoResponse
    info: ValidPathInfo
