"""Domain-specific scalar values whose wire representation is a string."""

from __future__ import annotations

from typing import Any, Self

from pydantic_core import core_schema


class WireScalar(str):
    """A typed daemon-protocol string with optional domain helper methods.

    Subclasses are native strings at runtime, avoiding a Pydantic model per
    scalar while still validating and serializing as strings in Pydantic models.
    """

    def __new__(cls, value: str = "") -> Self:
        return super().__new__(cls, value)

    @classmethod
    def from_wire(cls, value: str) -> Self:
        """Construct the domain value from its canonical wire string."""
        return cls(value)

    def to_wire(self) -> str:
        """Return the canonical daemon-protocol string."""
        return str(self)

    @classmethod
    def __get_pydantic_core_schema__(
        cls,
        _source_type: Any,
        _handler: Any,
    ) -> core_schema.CoreSchema:
        """Validate and serialize this scalar as its underlying string."""
        return core_schema.no_info_after_validator_function(
            cls.from_wire,
            core_schema.str_schema(),
            serialization=core_schema.to_string_ser_schema(),
        )
