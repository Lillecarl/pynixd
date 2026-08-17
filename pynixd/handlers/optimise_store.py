"""Handler for OptimiseStore (op 34)."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from ..serde import OptimiseStoreRequest
from ..serde.auth import Role
from ..serde.context import ReadContext
from ._base import Handler

if TYPE_CHECKING:
    from ..serde.context import RequestContext


class OptimiseStoreHandler(Handler):
    """Server handler for OptimiseStore — admin-only."""

    op: ClassVar[int] = 34

    async def handle(self, ctx: RequestContext) -> object | None:
        """Decode OptimiseStore request, verify admin auth, execute via daemon, return response."""
        req = await OptimiseStoreRequest.from_reader(
            ReadContext(reader=ctx.proxy.r, version=ctx.proxy.version, features=ctx.proxy.standard_features),
        )

        if ctx.role < Role.ADMIN:
            await ctx.proxy.send_error(
                "Operation 'OptimiseStore' requires administrative privileges.",
            )
            return None

        return await ctx.proxy.local_store.call(req)
