"""QueryRealisation operation — WireRequest/WireResponse types.

**The feature changes the message, and not only a value in it.**
`daemon.cc:1024` of the master branch reads a `DrvOutput` and writes an
`optional<UnkeyedRealisation>`: a tag, and then the body when the tag is 1.
Nix 2.34 writes a set of `Realisation`, each one a JSON string. The two share
no byte.

`RemoteStore::queryRealisationUncached` at `remote-store.cc:526` warns and
answers `nullptr` when the feature is off, so a client that asks pynixd for a
build trace gets nothing and no error. That is consequence 2 of issue #162,
and it stays true until pynixd claims the feature.
"""

from __future__ import annotations

from typing import ClassVar

from .constants import FEATURE_REALISATION_WITH_PATH
from .drv_output import DrvOutput
from .keyed_drv_output import KeyedDrvOutput
from .realisation import Realisation
from .unkeyed_realisation import UnkeyedRealisation
from .wire_message import WireField
from .wire_ops import WireRequest, WireResponse


class QueryRealisationResponse(WireResponse):
    """The build trace of one derivation output, in one of two shapes."""

    realisations: list[Realisation] = WireField(
        default_factory=list,
        unless_features=[FEATURE_REALISATION_WITH_PATH],
    )
    """The whole set, each entry a JSON string. This is the Nix 2.34 shape."""

    present: int = WireField(default=0, needs_features=[FEATURE_REALISATION_WITH_PATH])
    """The tag of `optional<UnkeyedRealisation>`: 0 for none, 1 for one.

    `WorkerProto::Serialise<std::optional<UnkeyedRealisation>>::read` at
    `worker-protocol.cc:512` reads it with `readNum<uint8_t>`, which takes the
    ordinary eight bytes of a Nix integer and then checks the range, so this
    is a plain integer field.
    """

    realisation: UnkeyedRealisation | None = WireField(
        default=None,
        needs_features=[FEATURE_REALISATION_WITH_PATH],
        wire_depends_on=lambda self: self.present == 1,
    )
    """The body, which the wire carries only when `present` is 1.

    The fields are read in the order they are declared, so `present` already
    holds its value when this gate runs.
    """


class QueryRealisationRequest(WireRequest):
    """QueryRealisation request — one derivation output, in one of two shapes."""

    op: ClassVar[int] = 43
    response_type = QueryRealisationResponse

    drv_output: DrvOutput | None = WireField(
        default=None,
        unless_features=[FEATURE_REALISATION_WITH_PATH],
    )
    """`"<drvHash>!<output>"` as one string. This is the Nix 2.34 shape."""

    keyed_drv_output: KeyedDrvOutput | None = WireField(
        default=None,
        needs_features=[FEATURE_REALISATION_WITH_PATH],
    )
    """The derivation path and the output name, as two strings."""
