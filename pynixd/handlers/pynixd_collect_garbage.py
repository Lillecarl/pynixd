"""Handler for PynixdCollectGarbage (op 101)."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from ..gc import Collector
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
        """Decode PynixdCollectGarbage request, verify admin auth, run the collector."""
        req = await PynixdCollectGarbageRequest.from_reader(
            ReadContext(reader=ctx.proxy.r, version=ctx.proxy.version, features=ctx.proxy.standard_features),
        )

        if ctx.role < Role.ADMIN:
            await ctx.proxy.send_error(
                "Operation 'PynixdCollectGarbage' requires administrative privileges.",
            )
            return None

        # The collector, and not the local store: op 101 is pynixd's own, and
        # the `nix daemon` under it answers `invalid operation 101`.
        return await Collector(ctx.proxy.ctx).run(req.action)
