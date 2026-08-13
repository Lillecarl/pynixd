"""Constants defined by the Nix daemon wire protocol."""

from __future__ import annotations

from typing import Final


def proto(major: int, minor: int) -> int:
    """Encode a daemon protocol version."""
    return (major << 8) | minor


def proto_str(version: int) -> str:
    """Render an encoded daemon protocol version."""
    return f"{version >> 8}.{version & 0xFF}"


WORKER_MAGIC_1: Final[int] = 0x6E697863
WORKER_MAGIC_2: Final[int] = 0x6478696F
PROTOCOL_VERSION: Final[int] = proto(1, 38)
MINIMUM_REMOTE_PROTOCOL: Final[int] = proto(1, 32)
SUPPORTED_PROTOCOL_VERSIONS: Final[tuple[int, ...]] = tuple(proto(1, minor) for minor in range(32, 39))


def is_supported_protocol(version: int) -> bool:
    """Return whether *version* is in the declared daemon codec interval."""
    return version in SUPPORTED_PROTOCOL_VERSIONS


STDERR_NEXT: Final[int] = 0x6F6C6D67
STDERR_LAST: Final[int] = 0x616C7473
STDERR_ERROR: Final[int] = 0x63787470
STDERR_START_ACTIVITY: Final[int] = 0x53545254
STDERR_STOP_ACTIVITY: Final[int] = 0x53544F50
STDERR_RESULT: Final[int] = 0x52534C54
