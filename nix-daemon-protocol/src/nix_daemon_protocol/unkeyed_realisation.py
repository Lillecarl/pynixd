"""UnkeyedRealisation — a build trace entry with no id, in the 1.38 feature shape.

**This shape exists only when `realisation-with-path-not-hash` is on.**
`WorkerProto::Serialise<UnkeyedRealisation>` at `worker-protocol.cc:485` of
the master branch raises when the negotiated set does not hold that name, and
it writes an output path and a set of signatures when it does. There is no
`dependentRealisations` field and no JSON.

Nix 2.34 has no such type on the wire at all: it carries a whole
`Realisation` as one JSON string, and `realisation.py` beside this holds that
shape. The two never appear on one connection, because the feature decides
which one the peers speak. Issue #162.
"""

from __future__ import annotations

from .signature import Signature
from .store_path import StorePath
from .wire_message import WireField, WireModel


class UnkeyedRealisation(WireModel):
    """The output path of a realisation, and the signatures over it."""

    out_path: StorePath = WireField(default_factory=StorePath, alias="outPath")
    signatures: set[Signature] = WireField(default_factory=set)
