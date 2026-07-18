"""Handler for AddBuildLog (op 45)."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from ..serde import AddBuildLogRequest
from ..serde.auth import Role
from ..serde.context import ReadContext
from ._base import Handler

if TYPE_CHECKING:
    from ..serde.context import RequestContext


class AddBuildLogHandler(Handler):
    """Server handler for AddBuildLog — admin-only."""

    op: ClassVar[int] = 45

    async def handle(self, ctx: RequestContext) -> object | None:
        """Decode AddBuildLog request, verify admin auth, execute via daemon, return response."""
        req = await AddBuildLogRequest.from_reader(
            ReadContext(reader=ctx.proxy.r, version=ctx.proxy.version),
        )

        if ctx.role < Role.ADMIN:
            await ctx.proxy.send_error(
                "Operation 'AddBuildLog' requires administrative privileges.",
            )
            return None

        return await ctx.proxy.local_store.call(req)
