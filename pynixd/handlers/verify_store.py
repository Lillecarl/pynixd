"""Handler for VerifyStore (op 35)."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from ..serde import VerifyStoreRequest
from ..serde.auth import Role
from ..serde.context import ReadContext
from ._base import Handler

if TYPE_CHECKING:
    from ..serde.context import RequestContext


class VerifyStoreHandler(Handler):
    """Server handler for VerifyStore — admin-only."""

    op: ClassVar[int] = 35

    async def handle(self, ctx: RequestContext) -> object | None:
        """Decode VerifyStore request, verify admin auth, execute via daemon, return response."""
        req = await VerifyStoreRequest.from_reader(
            ReadContext(reader=ctx.proxy.r, version=ctx.proxy.version, features=ctx.proxy.standard_features),
        )

        if ctx.role < Role.ADMIN:
            await ctx.proxy.send_error(
                "Operation 'VerifyStore' requires administrative privileges.",
            )
            return None

        return await ctx.proxy.local_store.call(req)
