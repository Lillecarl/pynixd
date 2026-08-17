"""Handler for CollectGarbage (op 20)."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from ..serde import CollectGarbageRequest
from ..serde.auth import Role
from ..serde.context import ReadContext
from ._base import Handler

if TYPE_CHECKING:
    from ..serde.context import RequestContext


class CollectGarbageHandler(Handler):
    """Server handler for CollectGarbage — admin-only."""

    op: ClassVar[int] = 20

    async def handle(self, ctx: RequestContext) -> object | None:
        """Decode CollectGarbage request, verify admin auth, execute via daemon, return response."""
        req = await CollectGarbageRequest.from_reader(
            ReadContext(reader=ctx.proxy.r, version=ctx.proxy.version),
        )

        if ctx.role < Role.ADMIN:
            await ctx.proxy.send_error(
                "Operation 'CollectGarbage' requires administrative privileges.",
            )
            return None

        # An idle connection keeps a worker of the daemon alive, and that
        # worker holds a temporary root for each path that it took. The
        # collector reads those roots and frees nothing. `nix-daemon` has no
        # such connection, because the client that made the root is gone.
        await ctx.proxy.local_store.retire_idle_connections()
        return await ctx.proxy.local_store.call(req)
