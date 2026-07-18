"""Handler for BuildDerivation (op 36)."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

import structlog

from ..serde import BuildDerivationRequest, BuildDerivationResponse
from ..serde.context import ReadContext
from ._base import Handler

if TYPE_CHECKING:
    from ..serde.context import RequestContext
    from ..serde.wire_ops import WireResponse

logger = structlog.get_logger(__name__)


class BuildDerivationHandler(Handler):
    """Server handler for BuildDerivation — scheduler or fallback to daemon."""

    op: ClassVar[int] = 36

    async def handle(self, ctx: RequestContext) -> WireResponse | None:
        """Decode BuildDerivation request, enqueue via scheduler or fallback to daemon, return result."""
        logger.debug("received_op")

        self_req = await BuildDerivationRequest.from_reader(ReadContext.from_request(ctx))

        if not ctx.proxy.use_scheduler_for_builds:
            logger.debug("handle_local_mode_fallback")
            result: BuildDerivationResponse = await ctx.proxy.local_store.execute(self_req, client=ctx.proxy.client)

            if result.result.status == 0:
                logger.debug("responded_op")
            return result

        if ctx.proxy.scheduler is None:
            raise RuntimeError("BuildDerivation requires a configured scheduler")

        build_id, future = await ctx.proxy.scheduler.build_derivation(self_req)
        subscribed = False
        if ctx.proxy.client is not None:
            subscribed = await ctx.proxy.scheduler.queue.subscribe(
                build_id,
                ctx.proxy.client,
                cancel_on_unsubscribe=True,
            )
        logger.info(
            "build_derivation_enqueued",
            build_id=build_id,
            drv_path=self_req.drv_path,
            required_count=len(self_req.derivation.input_srcs),
        )
        try:
            response = await future
            logger.debug("responded_op")
            return response
        finally:
            if subscribed and ctx.proxy.client is not None:
                await ctx.proxy.scheduler.queue.unsubscribe(build_id, ctx.proxy.client)
