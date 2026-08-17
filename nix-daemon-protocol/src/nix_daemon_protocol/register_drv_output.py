"""RegisterDrvOutput operation — WireRequest/WireResponse types.

**A keyed realisation is a `DrvOutput` and then an `UnkeyedRealisation`.**
`WorkerProto::Serialise<Realisation>` at `worker-protocol.cc:571` of the
master branch writes the two in that order, and both halves raise when
`realisation-with-path-not-hash` is off. Nix 2.34 writes the whole thing as
one JSON string.

This is the operation that the report on issue #162 saw raise: the backend
named the feature, pynixd named nothing back, and the codec of the backend
then refused the request that needed it.
"""

from __future__ import annotations

from typing import ClassVar

from .constants import FEATURE_REALISATION_WITH_PATH
from .keyed_drv_output import KeyedDrvOutput
from .realisation import Realisation
from .unkeyed_realisation import UnkeyedRealisation
from .wire_message import WireField
from .wire_ops import WireRequest, WireResponse


class RegisterDrvOutputResponse(WireResponse):
    """RegisterDrvOutput response — empty body, just stderr/WireLogs."""


class RegisterDrvOutputRequest(WireRequest):
    """RegisterDrvOutput request — one realisation, in one of two shapes."""

    op: ClassVar[int] = 42
    response_type = RegisterDrvOutputResponse

    realisation: Realisation | None = WireField(
        default=None,
        unless_features=[FEATURE_REALISATION_WITH_PATH],
    )
    """The whole realisation as one JSON string. This is the Nix 2.34 shape."""

    keyed_drv_output: KeyedDrvOutput | None = WireField(
        default=None,
        needs_features=[FEATURE_REALISATION_WITH_PATH],
    )
    """The id of the realisation: a derivation path and an output name."""

    unkeyed_realisation: UnkeyedRealisation | None = WireField(
        default=None,
        needs_features=[FEATURE_REALISATION_WITH_PATH],
    )
    """The body: an output path and a set of signatures.

    It follows the id, because `Serialise<Realisation>::write` writes the id
    first. The two are one value in Nix and two fields here, so the order of
    the declaration is the order of the wire.
    """
