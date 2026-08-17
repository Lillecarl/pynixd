"""Handler for AddToStoreNar (op 39)."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

import structlog

from ..serde.add_to_store_nar import (
    AddToStoreNarRequest,
    AddToStoreNarResponse,
)
from ..serde.context import ReadContext, WriteContext
from ..wire import forward_framed
from ._base import Handler

if TYPE_CHECKING:
    from ..serde.context import RequestContext

logger = structlog.get_logger(__name__)


class AddToStoreNarHandler(Handler):
    """Server handler for AddToStoreNar — streaming with NAR forwarding."""

    op: ClassVar[int] = 39

    async def handle(self, ctx: RequestContext) -> AddToStoreNarResponse | None:
        """Decode AddToStoreNar request, stream framed NAR to daemon, return response."""
        structlog.contextvars.bind_contextvars(operation=type(self).__name__)
        # **The connection that adds the path carries the options of the
        # client.** `LocalStore::addToStore` of Nix checks the signature of
        # each path against `trusted-public-keys`, and that setting reaches
        # the daemon through `SetOptions` alone. A transfer connection with no
        # options made the daemon read its own keys, so `nix copy --from` with
        # `--trusted-public-keys` was refused with "cannot add path ...
        # because it lacks a signature by a trusted key" for a path the cache
        # had signed correctly. `require-sigs` and `secret-key-files` travel
        # the same way. Issues #197 and #192.
        options = ctx.proxy.client.options if ctx.proxy.client is not None else None
        async with ctx.proxy.local_store.transfer_conn(options) as conn:
            await conn.apply_options(options)
            # 1. Read request header from client (serde)
            req = await AddToStoreNarRequest.from_reader(
                ReadContext(reader=ctx.proxy.r, version=ctx.proxy.version, features=ctx.proxy.standard_features),
            )

            # 2. Write request header to daemon
            await req.to_writer(WriteContext.from_conn(conn))
            await conn.w.drain()

            # 3. Forward framed NAR bytes from client to daemon
            await forward_framed(ctx.proxy.r, conn.w)

            # 4. Read response from daemon
            return await AddToStoreNarResponse.from_reader(
                ReadContext.from_conn(conn),
            )
