"""Handler for AddToStore (op 7)."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

import structlog

from ..serde import AddToStoreRequest
from ..serde.add_to_store import AddToStoreResponse as SerdeAddToStoreResponse
from ..serde.context import ReadContext, WriteContext
from ..serde.sign_path_info import SignPathInfoRequest as SerdeSignPathInfoRequest
from ..wire import forward_framed
from ._base import Handler

if TYPE_CHECKING:
    from ..serde.context import RequestContext

logger = structlog.get_logger(__name__)


class AddToStoreHandler(Handler):
    """Server handler for AddToStore — streaming with NAR forwarding."""

    op: ClassVar[int] = 7

    async def handle(self, ctx: RequestContext) -> SerdeAddToStoreResponse | None:
        """Decode AddToStore request, stream framed NAR to daemon, sign path info, cache result."""
        logger.debug("received_op")
        async with ctx.proxy.local_store.transfer_conn() as conn:
            # 1. Read request header from client (serde)
            req = await AddToStoreRequest.from_reader(
                ReadContext(reader=ctx.proxy.r, version=ctx.proxy.version),
            )

            # 2. Write request header to daemon
            await req.to_writer(WriteContext.from_conn(conn))
            await conn.w.drain()

            # 3. Forward framed NAR bytes from client to daemon
            await forward_framed(ctx.proxy.r, conn.w)

            # 4. Read response from daemon
            resp = await SerdeAddToStoreResponse.from_reader(
                ReadContext.from_conn(conn),
            )

        # 5. Sign path info and update cache (outside conn so we don't re-enter the pool)
        if resp.info is not None:
            sign_req = SerdeSignPathInfoRequest(info=resp.info)
            sign_resp = await ctx.proxy.local_store.execute(sign_req)
            resp.info = sign_resp.info

            ctx.proxy.local_store.add_path_info(resp.info)

        return resp
