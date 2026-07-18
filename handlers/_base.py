"""Handler base class and registry for server-side operation dispatch."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from ..serde.context import RequestContext

HANDLER_REGISTRY: dict[int, type[Handler]] = {}


class Handler(ABC):
    """Base class for operation handlers.

    Subclasses self-register in :data:`HANDLER_REGISTRY` via
    ``__init_subclass__`` when they define ``op`` in their own class body.
    """

    op: ClassVar[int]

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if "op" in cls.__dict__:
            HANDLER_REGISTRY[cls.op] = cls

    @abstractmethod
    async def handle(self, ctx: RequestContext) -> object | None:
        """Handle this operation from a client.

        Must consume the request body from ``ctx``, process it, and
        return a response (or None to signal the connection should close).
        """
        ...
