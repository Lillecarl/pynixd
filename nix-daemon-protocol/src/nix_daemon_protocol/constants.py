"""Constants defined by the Nix daemon wire protocol."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Iterable


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


# ── Protocol features ───────────────────────────────────────────────
#
# **The version number stopped at 1.38, and each new capability is a
# feature.** `worker-protocol.hh:105` of Nix says so: "you generally shouldn't
# change the protocol version number. Define a new
# `WorkerProto::Version::Feature` instead." Nix 2.34, Nix 2.35 and the master
# branch all report 1.38, and they differ by the feature set alone.
#
# Both sides send a set of names, and the negotiated set is the intersection.
# A feature that one side does not name is off, however new that side is.

FEATURE_EXCHANGE_PROTOCOL: Final[int] = proto(1, 38)
"""The protocol version at which both sides exchange a feature set.

`WorkerProto::BasicClientConnection::handshake` at
`worker-protocol-connection.cc:177` guards the exchange on this number, and
the server half at line 200 guards it the same way.
"""

FEATURE_REALISATION_WITH_PATH: Final[str] = "realisation-with-path-not-hash"
"""`DrvOutput` and `UnkeyedRealisation` carry a store path, and not JSON.

`WorkerProto::Serialise<DrvOutput>` of the master branch writes the derivation
path and the output name. `UnkeyedRealisation` writes the output path and the
signatures, and it drops `dependentRealisations`. Both codecs raise when the
feature is off, so a peer that does not name it gets no realisation at all.
"""

FEATURE_DELETE_DEAD_SPECIFIC_REFERRERS: Final[str] = "delete-dead-specific-referrers"
"""`CollectGarbage` may delete the referrers of a named set of paths."""

FEATURE_DISABLE_SET_OPTIONS: Final[str] = "disable-set-options"
"""`SetOptions` is a no-op, which is what recursive Nix needs."""

FEATURE_ADD_TO_STORE_SCANNING: Final[str] = "add-to-store-scanning"
"""The `AddToStoreScanning` operation, code 1001."""

FEATURE_SUBMIT_OUTPUT: Final[str] = "submit-output"
"""The `SubmitOutput` operation, code 1000."""

STANDARD_FEATURES: Final[frozenset[str]] = frozenset(
    {
        FEATURE_REALISATION_WITH_PATH,
        FEATURE_DELETE_DEAD_SPECIFIC_REFERRERS,
        FEATURE_DISABLE_SET_OPTIONS,
        FEATURE_ADD_TO_STORE_SCANNING,
        FEATURE_SUBMIT_OUTPUT,
    }
)
"""Each feature name that Nix defines, whether or not a peer offers it.

`WorkerProto::latest` of the master branch offers the first two. The other
three belong to `WorkerProto::builderRpcV0`, which is the connection that a
derivation gets under recursive Nix. Nix 2.34 offers none of the five.
"""

SUPPORTED_STANDARD_FEATURES: Final[frozenset[str]] = frozenset()
"""The standard features that this package has a codec for.

**It is empty, and that is the whole of the gap.** This package speaks the
Nix 2.34 shape of every operation, and Nix 2.34 offers no feature. A name
belongs here when the codec that the name gates is in this package, and not
before: a peer that reads the name then sends the new shape, and a codec that
cannot read it drops the connection. Issue #162.
"""


def negotiate_features(peer: Iterable[str], ours: Iterable[str]) -> frozenset[str]:
    """Answer the features that both sides named.

    This is `intersectFeatures` at `worker-protocol-connection.cc:148`. The
    advertisement of one side alone decides nothing.
    """
    return frozenset(peer) & frozenset(ours)


STDERR_NEXT: Final[int] = 0x6F6C6D67
STDERR_LAST: Final[int] = 0x616C7473
STDERR_ERROR: Final[int] = 0x63787470
STDERR_START_ACTIVITY: Final[int] = 0x53545254
STDERR_STOP_ACTIVITY: Final[int] = 0x53544F50
STDERR_RESULT: Final[int] = 0x52534C54
