"""Handler for SetOptions (op 19)."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from ..serde import SetOptionsRequest, SetOptionsResponse
from ..serde.auth import Role
from ..serde.context import ReadContext
from ..serde.logs import LogNext
from ._base import Handler

if TYPE_CHECKING:
    from ..serde.context import RequestContext


class SetOptionsHandler(Handler):
    """Server handler for SetOptions — admin-only, no-op for others."""

    op: ClassVar[int] = 19

    async def handle(self, ctx: RequestContext) -> object | None:
        """Keep the options of this session, and answer the client.

        **The options belong to the session, and not to one connection.**
        This sent the request over the pool, so it reached whichever
        connection was free, and every later operation of the client reached
        another one. A client that set `--post-build-hook` then saw the hook
        run for three of the five derivations that its request built, because
        pynixd built the five on several connections. Issue #192.

        `Connection.apply_options` now sends the set on the connection that is
        about to do the work, so nothing goes upstream here.
        """
        req = await SetOptionsRequest.from_reader(
            ReadContext(reader=ctx.proxy.r, version=ctx.proxy.version, features=ctx.proxy.standard_features),
        )

        if ctx.role < Role.ADMIN:
            resp = SetOptionsResponse()
            resp.logs.add(LogNext(text="pynixd: SetOptions ignored (no-op)"))
            return resp

        ctx.proxy.client.options = req
        return SetOptionsResponse()
