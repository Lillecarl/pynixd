"""Handler for AddPermRoot (op 47)."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from ..serde import AddPermRootRequest
from ..serde.add_perm_root import AddPermRootResponse
from ..serde.auth import Role
from ..serde.context import ReadContext
from ..serde.logs import LogNext
from ._base import Handler

if TYPE_CHECKING:
    from ..serde.context import RequestContext


class AddPermRootHandler(Handler):
    """Server handler for AddPermRoot — no-op for non-admin, forwards to daemon for admin."""

    op: ClassVar[int] = 47

    async def handle(self, ctx: RequestContext) -> object | None:
        """Decode AddPermRoot request, forward to daemon for admin, log no-op for others."""
        if ctx.role == Role.ADMIN:
            req = await AddPermRootRequest.from_reader(
                ReadContext(reader=ctx.proxy.r, version=ctx.proxy.version),
            )
            return await ctx.proxy.local_store.call(req)

        # Non-admin: consume request body, return no-op success
        await ctx.proxy.r.read_bytes()
        await ctx.proxy.r.read_bytes()
        resp = AddPermRootResponse(gc_root="")
        resp.logs.add(LogNext(text="pynixd: AddPermRoot ignored (no-op)"))
        return resp
