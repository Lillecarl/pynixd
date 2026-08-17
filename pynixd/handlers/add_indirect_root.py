"""Handler for AddIndirectRoot (op 12)."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from ..serde import AddIndirectRootRequest, AddIndirectRootResponse
from ..serde.auth import Role
from ..serde.context import ReadContext
from ._base import Handler

if TYPE_CHECKING:
    from ..serde.context import RequestContext


class AddIndirectRootHandler(Handler):
    """Server handler for AddIndirectRoot — admin forwards to daemon, non-admin no-op."""

    op: ClassVar[int] = 12

    async def handle(self, ctx: RequestContext) -> object | None:
        """Decode AddIndirectRoot request, forward to daemon for admin or return no-op for others."""
        if ctx.role == Role.ADMIN:
            req = await AddIndirectRootRequest.from_reader(
                ReadContext(reader=ctx.proxy.r, version=ctx.proxy.version, features=ctx.proxy.standard_features),
            )
            return await ctx.proxy.local_store.call(req)
        # Non-admin: consume request body, return no-op success
        await ctx.proxy.r.read_bytes()
        return AddIndirectRootResponse(value=1)  # type: ignore[return-value]
