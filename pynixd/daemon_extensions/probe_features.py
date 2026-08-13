"""ProbeFeatures operation — WireRequest/WireResponse types."""

from __future__ import annotations

from typing import ClassVar

from nix_daemon_protocol.wire_message import WireField
from nix_daemon_protocol.wire_ops import WireRequest, WireResponse


class ProbeFeaturesResponse(WireResponse):
    """ProbeFeatures response — supported features by system."""

    feature_matrix: dict[str, set[str]] = WireField(default_factory=dict)


class ProbeFeaturesRequest(WireRequest):
    """ProbeFeatures request — candidate systems and features to probe."""

    op: ClassVar[int] = 109
    is_extension: ClassVar[bool] = True
    response_type = ProbeFeaturesResponse
    systems: set[str] = WireField(default_factory=set)
    system_features: set[str] = WireField(default_factory=set)
