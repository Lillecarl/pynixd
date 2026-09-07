"""DerivationOutput — wire representation of a single derivation output."""

from __future__ import annotations

from enum import Enum, auto

from .wire_message import WireModel


class OutputKind(Enum):
    """Classification of a single derivation output."""

    INPUT_ADDRESSED = auto()
    CA_FIXED = auto()
    CA_FLOATING = auto()
    DEFERRED = auto()
    IMPURE = auto()


class DerivationOutput(WireModel):
    """Wire mirror of DerivationOutput. Three string fields on the wire."""

    path: str
    method: str = ""
    hash_digest: str = ""

    @property
    def kind(self) -> OutputKind:
        """Classify this output based on wire protocol fields."""
        if self.method == "":
            if self.path == "":
                return OutputKind.DEFERRED
            return OutputKind.INPUT_ADDRESSED
        if self.hash_digest == "impure":
            return OutputKind.IMPURE
        if self.hash_digest != "":
            return OutputKind.CA_FIXED
        return OutputKind.CA_FLOATING

    @property
    def is_ca(self) -> bool:
        return self.kind in (
            OutputKind.CA_FIXED,
            OutputKind.CA_FLOATING,
            OutputKind.IMPURE,
        )

    @property
    def is_text_hashed(self) -> bool:
        return self.method.startswith("text:")

    @property
    def is_fixed_ca(self) -> bool:
        return self.kind == OutputKind.CA_FIXED

    @property
    def is_floating_ca(self) -> bool:
        return self.kind == OutputKind.CA_FLOATING

    @property
    def is_deferred(self) -> bool:
        return self.kind == OutputKind.DEFERRED

    @property
    def is_impure(self) -> bool:
        return self.kind == OutputKind.IMPURE

    @property
    def is_dynamic_output(self) -> bool:
        return self.is_text_hashed and self.hash_digest == ""
