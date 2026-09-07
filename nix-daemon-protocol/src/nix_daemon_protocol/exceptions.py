"""Exceptions raised while decoding the daemon wire protocol."""


class DaemonProtocolError(Exception):
    """A daemon response contained a protocol-level error."""


class UnsupportedProtocolVersion(DaemonProtocolError):
    """A request is unavailable in the negotiated daemon protocol version."""
