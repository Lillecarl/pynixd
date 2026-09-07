"""The feature set of the handshake, and the gap that is left in it.

**The protocol number stopped at 1.38.** Nix 2.34, Nix 2.35 and the master
branch all report 1.38, and each new capability is a feature name instead.
`worker-protocol.hh:105` states the rule. So "which version of the protocol
does pynixd speak" is answered by the feature set, and not by the number.

The negotiated set is the intersection of the two sides,
`intersectFeatures` at `worker-protocol-connection.cc:148`. A peer that names
a feature gets it only when pynixd names it back. Issue #162.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import nix_daemon_protocol as ndp
from pynixd import wire
from pynixd.proxy import DaemonProxy

FEATURES_OF_NIX_LATEST = {
    ndp.FEATURE_REALISATION_WITH_PATH,
    ndp.FEATURE_DELETE_DEAD_SPECIFIC_REFERRERS,
}
"""What `WorkerProto::latest` of the master branch offers.

`worker-protocol.cc` builds it from these two names. A `nix` client and a
`nix-daemon` of that branch each send this set.
"""

FEATURES_OF_BUILDER_RPC_V0 = {
    ndp.FEATURE_REALISATION_WITH_PATH,
    ndp.FEATURE_DISABLE_SET_OPTIONS,
    ndp.FEATURE_ADD_TO_STORE_SCANNING,
    ndp.FEATURE_SUBMIT_OUTPUT,
}
"""What `WorkerProto::builderRpcV0` offers.

That set is frozen, because a change to it would be visible to a derivation.

**It is not recursive Nix.** `builder-rpc-v0` is a derivation feature of
dynamic derivations, and it is a much smaller surface. A derivation names it
in `requiredSystemFeatures`, and Nix then gives the builder a restricted
daemon socket and no output path in the environment. The builder registers
each output itself, with `SubmitOutput` and `AddToStoreScanning`, and it
cannot start a build through that socket. `docs/notes/reentrancy.md` holds
the detail, as Fact 9.
"""

MISSING_CODECS = {
    ndp.FEATURE_DELETE_DEAD_SPECIFIC_REFERRERS: (
        "`CollectGarbage` gains the referrers of a named set of paths, and `collect_garbage.py` has no field for them."
    ),
    ndp.FEATURE_DISABLE_SET_OPTIONS: (
        "`SetOptions` must become a no-op. A builder cannot change the settings of the daemon that serves it."
    ),
    ndp.FEATURE_ADD_TO_STORE_SCANNING: (
        "The `AddToStoreScanning` operation, code 1001, has no codec. A builder adds a store object with it, and "
        "Nix scans that object for references."
    ),
    ndp.FEATURE_SUBMIT_OUTPUT: (
        "The `SubmitOutput` operation, code 1000, has no codec. A builder registers one of its own outputs with it."
    ),
}
"""Each standard feature that pynixd does not claim, and the codec it needs.

**This is the whole of the wire gap above Nix 2.34.** Move a name out of here
when its codec lands, and add it to `SUPPORTED_STANDARD_FEATURES` in the same
change. The two must move together: a peer that reads the name then sends the
new shape, and a codec that cannot read that shape drops the connection.
"""


def test_the_number_is_the_one_that_nix_froze() -> None:
    """Nix 2.34 and the master branch both report 1.38."""
    assert wire.PROTOCOL_VERSION == wire.proto(1, 38)
    assert wire.FEATURE_EXCHANGE_PROTOCOL == wire.proto(1, 38)


def test_the_names_of_nix_are_the_names_of_this_package() -> None:
    """A typo in one name turns a feature off, with no error anywhere."""
    assert ndp.FEATURE_REALISATION_WITH_PATH == "realisation-with-path-not-hash"
    assert ndp.FEATURE_DELETE_DEAD_SPECIFIC_REFERRERS == "delete-dead-specific-referrers"
    assert ndp.FEATURE_DISABLE_SET_OPTIONS == "disable-set-options"
    assert ndp.FEATURE_ADD_TO_STORE_SCANNING == "add-to-store-scanning"
    assert ndp.FEATURE_SUBMIT_OUTPUT == "submit-output"
    assert ndp.STANDARD_FEATURES >= FEATURES_OF_NIX_LATEST | FEATURES_OF_BUILDER_RPC_V0


def test_the_ledger_names_every_feature_that_pynixd_does_not_claim() -> None:
    """The ledger and the claim are two halves of one statement."""
    assert set(MISSING_CODECS) == set(ndp.STANDARD_FEATURES) - set(ndp.SUPPORTED_STANDARD_FEATURES)
    for feature, reason in MISSING_CODECS.items():
        assert reason, feature


def test_pynixd_claims_the_build_trace_feature() -> None:
    """The first codec landed, and it is the one the report was about.

    `realisation-with-path-not-hash` covers `DrvOutput`,
    `UnkeyedRealisation`, `BuildResult.builtOutputs`, `QueryRealisation` and
    `RegisterDrvOutput`. Measured before it: the `ca` suite against Nix 2.35
    answered 2 of 24 through pynixd, and 19 of the 24 raised "the daemon is
    missing the 'realisation-with-path-not-hash' protocol feature".
    """
    assert ndp.SUPPORTED_STANDARD_FEATURES == frozenset({ndp.FEATURE_REALISATION_WITH_PATH})


def test_the_negotiation_is_the_intersection() -> None:
    """What one side names alone decides nothing."""
    assert wire.negotiate_features({"a", "b"}, {"b", "c"}) == {"b"}
    assert wire.negotiate_features({"a"}, set()) == frozenset()
    assert wire.negotiate_features(set(), {"a"}) == frozenset()


def test_a_client_of_the_master_branch_negotiates_the_build_trace() -> None:
    """A `nix` client of the master branch names both features of `latest`.

    pynixd names one of the two back, so a realisation survives the proxy and
    `delete-dead-specific-referrers` stays off. Issue #162.

    **This is what the codecs can do, and not what a proxy will claim.**
    `DaemonProxy.honourable_features` narrows it again to what every store
    that a build can go to offers, because pynixd honours a feature only when
    the backend reads the same shape.
    """
    negotiated = wire.negotiate_features(FEATURES_OF_NIX_LATEST, ndp.SUPPORTED_STANDARD_FEATURES)

    assert negotiated == frozenset({ndp.FEATURE_REALISATION_WITH_PATH})


class _Store:
    """A store, reduced to the two things `honourable_features` reads."""

    def __init__(self, *, no_schedule: bool, features: set[str]) -> None:
        self.no_schedule = no_schedule
        self.features = features


def _honourable(stores: dict[str, _Store]) -> frozenset[str]:
    """`DaemonProxy.honourable_features`, called on a proxy that is only stores."""
    proxy = SimpleNamespace(stores=stores)
    return DaemonProxy.honourable_features(cast("DaemonProxy", proxy))


FEATURE = ndp.FEATURE_REALISATION_WITH_PATH


def test_a_backend_that_offers_the_feature_lets_pynixd_claim_it() -> None:
    assert _honourable({"local": _Store(no_schedule=False, features={FEATURE})}) == frozenset({FEATURE})


def test_one_backend_that_does_not_offer_it_takes_it_away() -> None:
    """pynixd honours a feature by speaking its shape to the backend as well.

    A client on the new shape and a backend on the old one would need pynixd
    to translate, and one direction of that has no answer on the wire: the
    old `DrvOutput` carries the hash of the derivation and the new one carries
    the path. Issue #162, step 4.
    """
    stores = {
        "new": _Store(no_schedule=False, features={FEATURE}),
        "old": _Store(no_schedule=False, features=set()),
    }

    assert _honourable(stores) == frozenset()


def test_a_substituter_takes_nothing_away() -> None:
    """A binary cache is not a peer of the worker protocol.

    Its feature set is empty for every configuration, so counting it would
    answer "nothing" wherever a cache is configured — which is nearly
    everywhere.
    """
    stores = {
        "local": _Store(no_schedule=False, features={FEATURE}),
        "http-cache.nixos.org": _Store(no_schedule=True, features=set()),
    }

    assert _honourable(stores) == frozenset({FEATURE})


def test_a_store_that_never_connected_is_read_as_offering_nothing() -> None:
    """The conservative answer, and not a wrong one.

    `DaemonStore._features` is empty until the first handshake. pynixd then
    names no feature, and both sides keep the shape that every version reads.
    """
    assert _honourable({"local": _Store(no_schedule=False, features=set())}) == frozenset()
