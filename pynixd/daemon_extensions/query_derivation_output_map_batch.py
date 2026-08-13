"""QueryDerivationOutputMapBatch operation — WireRequest/WireResponse types."""

from __future__ import annotations

from typing import ClassVar

from nix_daemon_protocol.store_path import StorePath  # noqa: TC001
from nix_daemon_protocol.wire_message import WireField
from nix_daemon_protocol.wire_ops import WireRequest, WireResponse


class DerivationOutputMapBatchResponse(WireResponse):
    """Response — nested dict: drv_path → {output_name → StorePath}."""

    outputs: dict[StorePath, dict[str, StorePath]] = WireField(default_factory=dict)


class QueryDerivationOutputMapBatchRequest(WireRequest):
    """QueryDerivationOutputMapBatch request — set of derivation StorePaths."""

    op: ClassVar[int] = 106
    is_extension: ClassVar[bool] = True
    response_type = DerivationOutputMapBatchResponse
    drv_paths: set[StorePath] = WireField(default_factory=set)
