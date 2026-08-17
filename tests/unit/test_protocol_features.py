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

import nix_daemon_protocol as ndp
from pynixd import wire

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
    ndp.FEATURE_REALISATION_WITH_PATH: (
        "`DrvOutput` is a store path and an output name, and "
        "`UnkeyedRealisation` is an output path and a set of signatures with "
        "no `dependentRealisations`. `realisation.py` writes the JSON shape "
        "of Nix 2.34 instead."
    ),
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


def test_pynixd_claims_no_standard_feature_yet() -> None:
    """Nix 2.34 claims none either, so the floor sees the same bytes.

    This test states the gap rather than guards it. Delete the assertion when
    the first codec lands, and leave the ledger above.
    """
    assert ndp.SUPPORTED_STANDARD_FEATURES == frozenset()


def test_the_negotiation_is_the_intersection() -> None:
    """What one side names alone decides nothing."""
    assert wire.negotiate_features({"a", "b"}, {"b", "c"}) == {"b"}
    assert wire.negotiate_features({"a"}, set()) == frozenset()
    assert wire.negotiate_features(set(), {"a"}) == frozenset()


def test_a_client_of_the_master_branch_negotiates_nothing_today() -> None:
    """The measured consequence of the gap, stated as an expectation.

    A `nix` client of the master branch names both features of `latest`.
    pynixd names neither back, so `queryRealisation` through pynixd answers
    nothing and `RegisterDrvOutput` raises. Issue #162 holds the report.
    """
    negotiated = wire.negotiate_features(FEATURES_OF_NIX_LATEST, ndp.SUPPORTED_STANDARD_FEATURES)

    assert negotiated == frozenset()
