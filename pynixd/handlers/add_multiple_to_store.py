"""Handler for AddMultipleToStore (op 44)."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, ClassVar

import anyio
import structlog

from ..serde.add_multiple_to_store import (
    AddMultipleToStoreRequest,
    AddMultipleToStoreResponse,
)
from ..serde.context import ReadContext, WriteContext
from ..serde.valid_path_info import ValidPathInfo
from ..wire import FramedReader, FramedWriter, NixReader, NixWriter
from ._base import Handler

if TYPE_CHECKING:
    from ..serde.context import RequestContext

logger = structlog.get_logger(__name__)


class AddMultipleToStoreHandler(Handler):
    """Server handler for AddMultipleToStore — streaming with framed NAR forwarding."""

    op: ClassVar[int] = 44

    async def handle(self, ctx: RequestContext) -> AddMultipleToStoreResponse | None:
        """Decode AddMultipleToStore request, stream framed NAR payloads to daemon, cache path infos, return response."""
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
            req = await AddMultipleToStoreRequest.from_reader(
                ReadContext(reader=ctx.proxy.r, version=ctx.proxy.version, features=ctx.proxy.standard_features),
            )

            # 2. Write request header to daemon
            await req.to_writer(WriteContext.from_conn(conn))
            await conn.w.drain()

            # 3. Concurrently: forward payload + read daemon response
            # An anyio task group hands back no task object, so the child
            # records its result in this list. The group waits for the child
            # on exit, which is what `await resp_task` did before.
            responses: list[AddMultipleToStoreResponse] = []

            async def _read_response() -> None:
                responses.append(
                    await AddMultipleToStoreResponse.from_reader(
                        ReadContext.from_conn(conn),
                    ),
                )

            async with anyio.create_task_group() as tg:
                tg.start_soon(_read_response)
                infos = await self._forward_stream(ctx.proxy.r, conn.w)

            if not responses:
                raise RuntimeError("the AddMultipleToStore reader task recorded no response")
            resp = responses[0]

            # Update path info cache
            ctx.proxy.local_store.add_path_infos(infos)

            return resp

    async def _forward_stream(
        self,
        src: NixReader,
        dst: NixWriter,
    ) -> list[ValidPathInfo]:
        """Forward AddMultipleToStore payload, snooping ValidPathInfos.

        Payload structure after the header:
            [count:uint64][path_info_bytes + nar_bytes]...[0-size terminator]
        """
        fsrc = FramedReader(src)
        fdst = FramedWriter(dst)

        expected = await fsrc.read_uint64()
        fdst.write_uint64(expected)
        logger.debug("add_multiple_forward_start", expected=expected)

        infos: list[ValidPathInfo] = []
        for _ in range(expected):
            info = await ValidPathInfo.from_reader(ReadContext(reader=fsrc, version=1))
            infos.append(info)
            fdst.write(await info.bytes_wire())
            sent_bytes = 0
            while sent_bytes < info.info.nar_size:
                read = min(info.info.nar_size - sent_bytes, 1024 * 1024)
                data = await fsrc.readexactly(read)
                fdst.write(data)
                sent_bytes += len(data)

        await fdst.finalize()
        try:
            await asyncio.wait_for(fsrc.ensure_eof(), timeout=10)
        except TimeoutError:
            logger.warning("add_multiple_forward_source_eof_timeout", count=len(infos))
        return infos
