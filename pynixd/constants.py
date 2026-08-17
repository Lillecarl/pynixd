"""
Nix daemon protocol constants and magic numbers.
"""

from typing import Final

# The feature names are wire constants, and `nix_daemon_protocol` holds them
# with the reference to the Nix source of each one. This module states the
# rest of the wire constants itself, and a second copy of the feature names
# would be a second thing to keep true.
from nix_daemon_protocol.constants import (
    FEATURE_ADD_TO_STORE_SCANNING as FEATURE_ADD_TO_STORE_SCANNING,
    FEATURE_DELETE_DEAD_SPECIFIC_REFERRERS as FEATURE_DELETE_DEAD_SPECIFIC_REFERRERS,
    FEATURE_DISABLE_SET_OPTIONS as FEATURE_DISABLE_SET_OPTIONS,
    FEATURE_EXCHANGE_PROTOCOL as FEATURE_EXCHANGE_PROTOCOL,
    FEATURE_REALISATION_WITH_PATH as FEATURE_REALISATION_WITH_PATH,
    FEATURE_SUBMIT_OUTPUT as FEATURE_SUBMIT_OUTPUT,
    STANDARD_FEATURES as STANDARD_FEATURES,
    SUPPORTED_STANDARD_FEATURES as SUPPORTED_STANDARD_FEATURES,
    negotiate_features as negotiate_features,
)


def proto(major: int, minor: int) -> int:
    """Encode a protocol version as a single int."""
    return (major << 8) | minor


def proto_str(version: int) -> str:
    """Format a protocol version int as 'major.minor'."""
    return f"{version >> 8}.{version & 0xFF}"


# ── Protocol Magic ──────────────────────────────────────────────────

WORKER_MAGIC_1: Final[int] = 0x6E697863  # client hello
WORKER_MAGIC_2: Final[int] = 0x6478696F  # server hello
PROTOCOL_VERSION: Final[int] = proto(1, 38)
MINIMUM_REMOTE_PROTOCOL: Final[int] = proto(1, 32)


# ── Stderr message types ──────────────────────────────────────────

STDERR_NEXT: Final[int] = 0x6F6C6D67
STDERR_LAST: Final[int] = 0x616C7473
STDERR_ERROR: Final[int] = 0x63787470
STDERR_START_ACTIVITY: Final[int] = 0x53545254
STDERR_STOP_ACTIVITY: Final[int] = 0x53544F50
STDERR_RESULT: Final[int] = 0x52534C54
