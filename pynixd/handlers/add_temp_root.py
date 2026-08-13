"""Handler for AddTempRoot (op 11)."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from ..serde import AddTempRootRequest, AddTempRootResponse
from ..serde.auth import Role
from ..serde.context import ReadContext
from ._base import Handler

if TYPE_CHECKING:
    from ..serde.context import RequestContext


class AddTempRootHandler(Handler):
    """Server handler for AddTempRoot — admin forwards to daemon, non-admin no-op."""

    op: ClassVar[int] = 11

    async def handle(self, ctx: RequestContext) -> object | None:
        """Decode AddTempRoot request, forward to daemon for admin, return no-op for others."""
        if ctx.role == Role.ADMIN:
            req = await AddTempRootRequest.from_reader(
                ReadContext(reader=ctx.proxy.r, version=ctx.proxy.version),
            )
            return await ctx.proxy.local_store.call(req)
        # Non-admin: consume request body, return no-op success
        await ctx.proxy.r.read_bytes()
        return AddTempRootResponse(value=1)  # type: ignore[return-value]
