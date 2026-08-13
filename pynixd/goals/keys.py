"""Stable goal identity keys."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EnsureDerivedPathKey:
    """Deduplication key for EnsureDerivedPathGoal instances."""

    derived_path: str
    substituter_fingerprint: str


@dataclass(frozen=True)
class BuildDerivationKey:
    """Deduplication key for BuildDerivationGoal instances."""

    drv_path: str
    derivation_fingerprint: str


@dataclass(frozen=True)
class SubstitutePathKey:
    """Deduplication key for SubstitutePathGoal instances."""

    path: str
    substituter_fingerprint: str
