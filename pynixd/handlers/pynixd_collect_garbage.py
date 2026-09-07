"""Handler for PynixdCollectGarbage (op 101)."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from ..serde import PynixdCollectGarbageRequest
from ..serde.auth import Role
from ..serde.context import ReadContext
from ._base import Handler

if TYPE_CHECKING:
    from ..serde.context import RequestContext


class PynixdCollectGarbageHandler(Handler):
    """Server handler for PynixdCollectGarbage — admin-only."""

    op: ClassVar[int] = 101

    async def handle(self, ctx: RequestContext) -> object | None:
        """Decode PynixdCollectGarbage request, verify admin auth, execute via daemon, return response."""
        req = await PynixdCollectGarbageRequest.from_reader(
            ReadContext(reader=ctx.proxy.r, version=ctx.proxy.version, features=ctx.proxy.standard_features),
        )

        if ctx.role < Role.ADMIN:
            await ctx.proxy.send_error(
                "Operation 'PynixdCollectGarbage' requires administrative privileges.",
            )
            return None

        # The same reason as `CollectGarbageHandler`: an idle connection holds
        # the temporary roots of the worker under it.
        await ctx.proxy.local_store.retire_idle_connections()
        return await ctx.proxy.local_store.call(req)
