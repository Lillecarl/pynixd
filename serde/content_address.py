"""ContentAddress — a content-addressed store path identifier.

Wire format: plain string like ``text:sha256`` or ``fixed:r:sha256``.
"""

from __future__ import annotations

from .wire_string import WireString


class ContentAddress(WireString):
    """Content address method with algorithm — plain string on the wire."""

    value: str
