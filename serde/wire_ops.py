"""WireRequest / WireResponse — base classes for Nix daemon operations.

These live in their own module to avoid circular imports: they depend on
both ``wire_message`` (WireModel, WireField) and ``logs`` (WireLogs).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from .logs import WireLogs
from .wire_message import WireField, WireModel

if TYPE_CHECKING:
    from .context import ReadContext, WriteContext

WIRE_REGISTRY: dict[int, type[WireRequest]] = {}


class WireRequest(WireModel):
    """Base class for Nix daemon protocol requests.

    Subclasses must override ``op`` and ``response_type``::

        class SomeRequest(WireRequest):
            op: ClassVar[int] = 42
            response_type: ClassVar[type[SomeResponse]] = SomeResponse
            path: StorePath
    """

    op: ClassVar[int]
    name: ClassVar[str]
    response_type: ClassVar[type]
    forward: ClassVar[bool] = True
    is_extension: ClassVar[bool] = False
    is_query: ClassVar[bool] = False

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if "name" not in cls.__dict__:
            cls.name = cls.__name__.removesuffix("Request")
        if "op" in cls.__dict__:
            WIRE_REGISTRY[cls.op] = cls

    async def to_writer(self, ctx: WriteContext) -> None:
        """Write op code then body."""
        ctx.writer.write_uint64(self.op)
        await super().to_writer(ctx)

    @classmethod
    async def from_reader(cls, ctx: ReadContext):
        """Read body only — op was consumed by dispatch."""
        return await super().from_reader(ctx)


class WireResponse(WireModel):
    """Base class for Nix daemon protocol responses.

    The ``logs`` field is a ``WireLogs`` (stderr stream).  Because it is a
    ``WireModel`` the generic serde engine handles it automatically —
    ``to_writer`` writes the full stderr stream before the body fields,
    ``from_reader`` reads the stream before the body fields.

    Subclasses define wire-body fields as normal::

        class SomeResponse(WireResponse):
            valid: bool
    """

    logs: WireLogs = WireField(default_factory=WireLogs)

    @property
    def is_not_found(self) -> bool:
        """True when extension fallback should continue to another store."""
        return False
