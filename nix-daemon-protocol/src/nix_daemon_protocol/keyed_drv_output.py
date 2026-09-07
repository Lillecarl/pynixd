"""KeyedDrvOutput — a derivation output identifier in the 1.38 feature shape.

**A `DrvOutput` names a derivation path and an output name here, and not a
hash.** `WorkerProto::Serialise<DrvOutput>` at `worker-protocol.cc:544` of the
master branch writes the two, and it raises when
`realisation-with-path-not-hash` is off. `drv_output.py` beside this holds the
other shape, which is one string of the form ``<hash>!<output>``.

The all-zero `sha256:0000…0000!out` of the report on issue #162 is what the
old form becomes when a peer builds it from a derivation whose hash it does
not have. The new form has no place to put a hash, so the question does not
arise.
"""

from __future__ import annotations

from .store_path import StorePath
from .wire_message import WireField, WireModel


class KeyedDrvOutput(WireModel):
    """The derivation that made an output, and the name of that output."""

    drv_path: StorePath = WireField(default_factory=StorePath, alias="drvPath")
    output_name: str = WireField(default="", alias="outputName")
