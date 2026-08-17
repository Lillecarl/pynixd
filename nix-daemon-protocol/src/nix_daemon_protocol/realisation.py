"""Realisation — JSON-encoded derivation realisation on the Nix daemon wire."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import ConfigDict

from .drv_output import DrvOutput
from .logging import deserialization_scope
from .store_path import StorePath
from .wire_message import WireField, WireModel

if TYPE_CHECKING:
    from .context import ReadContext, WriteContext


class Realisation(WireModel):
    """A Realisation on the Nix daemon wire.

    Wire format: length-prefixed UTF-8 containing a JSON object.
    ``id`` and ``dependentRealisations`` keys/values are plain strings
    on the wire (e.g. ``"sha256-abc123!out"``), matching the old
    framework's JSON serialization.
    """

    # **The name of a field builds one of these, and the alias does too.**
    # The wire uses the alias, and Python code reads `realisation.out_path`.
    # With the alias alone, `Realisation(out_path=...)` put nothing in the
    # field and raised nothing, so the answer carried `null` where a store
    # path belongs.
    model_config = ConfigDict(validate_by_alias=True, validate_by_name=True)

    id: DrvOutput = WireField(default_factory=DrvOutput)
    out_path: StorePath | None = WireField(default=None, alias="outPath")
    signatures: list[str] = WireField(default_factory=list)
    dependent_realisations: dict[str, str] = WireField(default_factory=dict, alias="dependentRealisations")

    @classmethod
    async def from_reader(cls, ctx: ReadContext):
        with deserialization_scope(ctx, cls):
            raw = await ctx.reader.read_bytes()
            return cls.from_json(raw)

    async def to_writer(self, ctx: WriteContext) -> None:
        ctx.writer.write_bytes(self.__pydantic_serializer__.to_json(self, by_alias=True))

    def to_json(self, **kwargs) -> str:
        kwargs.setdefault("by_alias", True)
        return self.model_dump_json(**kwargs)
