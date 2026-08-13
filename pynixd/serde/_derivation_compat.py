"""Temporary pynixd behaviors layered on the standalone wire model."""

from __future__ import annotations

from nix_daemon_protocol.basic_derivation import BasicDerivation

from ..store_path import StorePath
from ..system_features import PYNIXD_HANDLED_FEATURES


def _effective_required_features(self: BasicDerivation) -> set[str]:
    return self.required_system_features - PYNIXD_HANDLED_FEATURES


def _output_paths(self: BasicDerivation) -> dict[str, StorePath]:
    return {name: StorePath(output.path) for name, output in self.outputs.items()}


BasicDerivation.effective_required_features = property(_effective_required_features)  # type: ignore[attr-defined]
BasicDerivation.output_paths = _output_paths  # type: ignore[attr-defined]
