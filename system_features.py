"""Nix system features, system platforms, and requiredSystemFeatures.

System features are arbitrary strings used to match derivations to builders.
A derivation declares `requiredSystemFeatures` and a builder advertises
`system-features`; a builder is only eligible if its features are a superset.

This module defines constants for the known/standard feature strings and
platform strings. At runtime, features are kept as plain strings so
arbitrary/custom feature strings (not in this module) are fully supported.
"""

from __future__ import annotations


class SystemFeature:
    """Known Nix system feature string constants.

    Standard features are auto-added by Nix when the corresponding
    condition is true. Custom features can be set in nix.conf.

    Use these constants for comparison and construction, but feature
    sets are ``set[str]`` — any string is valid.
    """

    NIXOS_TEST = "nixos-test"
    BENCHMARK = "benchmark"
    BIG_PARALLEL = "big-parallel"
    UID_RANGE = "uid-range"
    KVM = "kvm"
    APPLE_VIRT = "apple-virt"
    CA_DERIVATIONS = "ca-derivations"
    DYNAMIC_DERIVATIONS = "dynamic-derivations"
    RECURSIVE_NIX = "recursive-nix"


# All known feature strings for validation/display
KNOWN_FEATURES: frozenset[str] = frozenset(
    {
        SystemFeature.NIXOS_TEST,
        SystemFeature.BENCHMARK,
        SystemFeature.BIG_PARALLEL,
        SystemFeature.UID_RANGE,
        SystemFeature.KVM,
        SystemFeature.APPLE_VIRT,
        SystemFeature.CA_DERIVATIONS,
        SystemFeature.DYNAMIC_DERIVATIONS,
        SystemFeature.RECURSIVE_NIX,
    },
)

# Features that pynixd handles before sending builds to backend stores.
# These can be safely stripped from requiredSystemFeatures because pynixd
# resolves the derivation (e.g., CA derivations → InputAddressed) or
# manages the trampoline (for dynamic derivations) before forwarding or
# after receiving results. The backend daemon never needs to see the
# experimental semantics.
#
# recursive-nix is NOT here — the builder truly needs nix available at
# runtime to execute recursive builds.
#
# ca-derivations is NOT here — Lix's daemon cannot parse floating CA
# outputs in BuildDerivation, so the allocator must keep this feature
# to correctly exclude incompatible builders.
PYNIXD_HANDLED_FEATURES: frozenset[str] = frozenset(
    {
        SystemFeature.DYNAMIC_DERIVATIONS,
    },
)

# Platforms to probe when discovering store capabilities.
# Each is a Nix system triple (machine-kernel).
PROBE_SYSTEMS: frozenset[str] = frozenset(
    {
        "x86_64-linux",
        "aarch64-linux",
        "aarch64-darwin",
    },
)
