"""pynixd - Python Nix daemon protocol proxy."""

import json as _json

from .instance import Server

__all__ = ["Server"]


# ── Global JSON encoder patch: handle objects with __json__() ──────────
# Allows any class (e.g. StorePath) to define __json__() and be
# transparently serializable via json.dumps() without passing default=str
# at every call site.

_original_json_default = _json.JSONEncoder.default  # type: ignore[assignment]


def _patched_json_default(  # type: ignore[override]
    self: _json.JSONEncoder,
    o: object,
) -> object:
    """Fallback serializer that delegates to ``__json__()`` if available."""
    if hasattr(o, "__json__"):
        return o.__json__()  # type: ignore[no-any-return, attr-defined]
    return _original_json_default(self, o)


_json.JSONEncoder.default = _patched_json_default  # type: ignore[method-assign]
_json._default_encoder = _json.JSONEncoder()  # type: ignore[attr-defined]
