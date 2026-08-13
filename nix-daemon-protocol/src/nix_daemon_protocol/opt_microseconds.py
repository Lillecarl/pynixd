"""OptMicroseconds — tagged-optional uint64 for microseconds."""

from __future__ import annotations

from .wire_message import WireField, WireModel


class OptMicroseconds(WireModel):
    """Optional microseconds — [tag uint64][value uint64 if tag==1].

    tag=1 = present, tag=0 = absent.
    value is only on the wire when tag==1.
    """

    tag: int = 0
    value: int | None = WireField(default=None, wire_depends_on=lambda self: self.tag == 1)
