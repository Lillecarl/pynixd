"""ContentAddress — a content-addressed store path identifier.

Wire format: plain string like ``text:sha256`` or ``fixed:r:sha256``.
"""

from __future__ import annotations

from .wire_scalar import WireScalar


class ContentAddress(WireScalar):
    """Content address method with algorithm — plain string on the wire."""

    def __new__(cls, value: str = "", *, content_address: str | None = None) -> ContentAddress:
        if content_address is not None:
            if value and value != content_address:
                raise ValueError("ContentAddress value and content_address disagree")
            value = content_address
        return super().__new__(cls, value)

    @property
    def value(self) -> str:
        """Compatibility spelling for the canonical string value."""
        return self
