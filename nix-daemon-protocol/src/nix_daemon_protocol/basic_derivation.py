"""BasicDerivation — wire mirror of a Nix derivation."""

from __future__ import annotations

import json

from .derivation_output import DerivationOutput
from .store_path import StorePath
from .wire_message import WireField, WireModel


class BasicDerivation(WireModel):
    """Wire mirror of BasicDerivation."""

    outputs: dict[str, DerivationOutput] = WireField(default_factory=dict)
    input_srcs: set[StorePath] = WireField(default_factory=set)
    platform: str
    builder: str
    args: list[str] = WireField(default_factory=list)
    env: dict[str, str] = WireField(default_factory=dict)
    is_dynamic: bool = WireField(default=False, serialize=False, deserialize=False)

    @property
    def requires_nix(self) -> bool:
        return not self.supports_lix()

    @property
    def build_local(self) -> bool:
        return self.env.get("pynixd_fast") == "1" or self.env.get("preferLocalBuild") == "1"

    @property
    def required_system_features(self) -> set[str]:
        raw = self.env.get("requiredSystemFeatures", "")
        if not raw:
            return set()
        return set(raw.split())

    def supports_lix(self) -> bool:
        if self.is_dynamic:
            return False
        return all(not (output.is_ca or output.is_deferred) for output in self.outputs.values())

    def to_stats_json(self) -> str:
        return json.dumps(
            {
                "builder": self.builder,
                "outputs": list(self.outputs.keys()),
                "system": self.env.get("system", self.platform),
            },
            sort_keys=True,
        )

    @property
    def has_dynamic_outputs(self) -> bool:
        return any(output.is_dynamic_output for output in self.outputs.values())

    @property
    def has_ca_floating(self) -> bool:
        return any(output.is_floating_ca and not output.is_text_hashed for output in self.outputs.values())

    @property
    def has_deferred(self) -> bool:
        return any(output.is_deferred for output in self.outputs.values())

    @property
    def has_impure(self) -> bool:
        return any(output.is_impure for output in self.outputs.values())

    @property
    def has_text_hashed(self) -> bool:
        return any(output.is_text_hashed for output in self.outputs.values())
