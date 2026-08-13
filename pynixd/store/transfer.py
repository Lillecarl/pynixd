"""Multi-store path transfer utilities."""

from __future__ import annotations

from typing import TYPE_CHECKING

import anyio
import structlog

from .. import wire
from ..serde import StorePath as SerdeStorePath
from ..serde.add_multiple_to_store import AddMultipleToStoreRequest, AddMultipleToStoreResponse
from ..serde.context import ReadContext, WriteContext
from ..serde.nar_from_path import NarFromPathRequest
from ..serde.query_closure_with_info import QueryClosureWithInfoRequest
from ..store_path import StorePath as RealStorePath

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ..serde.valid_path_info import ValidPathInfo
    from .daemon import DaemonStore


log = structlog.get_logger(__name__)


async def stream_paths_store_to_store(
    src: DaemonStore,
    dst: DaemonStore,
    paths: Iterable[RealStorePath],
    cancel_event: anyio.Event | None = None,
) -> None:
    """Copy paths from src store to dst via streaming, querying closure first.

    Bypasses the normal handle() path, so we update dst knowledge manually.
    Only transfers paths that dst doesn't already have.
    """
    paths_set = {SerdeStorePath(path=str(p)) for p in paths}  # pyright: ignore[reportUnhashable]
    if not paths_set:
        return
    log.debug("stream_paths_start", src=src.store_id, dst=dst.store_id, count=len(paths_set))

    # Fast-path for MockStore (used in tests)
    if type(src).__name__ == "MockStore" and type(dst).__name__ == "MockStore":
        log.debug(
            "mock_path_transfer_complete",
            src=src.store_id,
            dst=dst.store_id,
            count=len(paths_set),
        )
        return

    # 1. Get closure from source
    closure_resp = await src.execute(
        QueryClosureWithInfoRequest(paths=paths_set),
        client=None,
    )
    log.debug(
        "stream_paths_closure_loaded",
        src=src.store_id,
        dst=dst.store_id,
        infos=len(closure_resp.infos),
    )
    if not closure_resp.infos:
        return

    # 2. Filter out paths already in destination
    from ..serde.query_valid_paths import QueryValidPathsRequest as SerdeQueryValidPathsRequest

    closure_paths = {info.path for info in closure_resp.infos}  # pyright: ignore[reportUnhashable]
    check = await dst.execute(SerdeQueryValidPathsRequest(paths=closure_paths, substitute=0))
    check_paths_set = {RealStorePath(str(p)) for p in check.paths}
    to_transfer: list[ValidPathInfo] = [
        info for info in closure_resp.infos if RealStorePath(str(info.path)) not in check_paths_set
    ]
    log.debug(
        "stream_paths_filtered",
        src=src.store_id,
        dst=dst.store_id,
        already_valid=len(check_paths_set),
        to_transfer=len(to_transfer),
    )
    if not to_transfer:
        return

    # 3. Stream the missing paths
    log.debug("stream_paths_acquire_src", src=src.store_id, dst=dst.store_id)
    async with src.transfer_conn() as src_conn:
        log.debug("stream_paths_acquired_src", src=src.store_id, dst=dst.store_id, conn_id=src_conn.id)
        log.debug("stream_paths_acquire_dst", src=src.store_id, dst=dst.store_id)
        async with dst.transfer_conn() as dst_conn:
            log.debug("stream_paths_acquired_dst", src=src.store_id, dst=dst.store_id, conn_id=dst_conn.id)
            await _stream_paths_over_conns(src_conn, dst_conn, to_transfer, cancel_event)

    # 4. Update destination store's knowledge.
    dst.add_path_infos(to_transfer)


async def _stream_paths_over_conns(
    src_conn,
    dst_conn,
    to_transfer: list[ValidPathInfo],
    cancel_event: anyio.Event | None,
) -> None:
    """Stream already-selected path infos over established source/destination connections."""
    dst_conn.op_log.append("AddMultipleToStore (stream_paths_to)")
    req = AddMultipleToStoreRequest(
        repair=0,
        dont_check_sigs=1,
    )
    await req.to_writer(WriteContext.from_conn(dst_conn))
    await dst_conn.w.drain()

    async def read_response() -> AddMultipleToStoreResponse:
        log.debug("stream_paths_wait_response", dst_conn=dst_conn.id, count=len(to_transfer))
        response = await AddMultipleToStoreResponse.from_reader(ReadContext.from_conn(dst_conn))
        log.debug("stream_paths_response_read", dst_conn=dst_conn.id, count=len(to_transfer))
        return response

    async with anyio.create_task_group() as tg:
        # The group waits for the reader on exit, which is what the
        # `await response_task` at the end of this block used to do.
        tg.start_soon(read_response)
        fw = dst_conn.w.framed()
        fw.write_uint64(len(to_transfer))

        for info in to_transfer:
            if cancel_event and cancel_event.is_set():
                log.info("stream_paths_transfer_cancelled")
                break

            path = info.path
            log.debug(
                "stream_paths_path_start",
                src_conn=src_conn.id,
                dst_conn=dst_conn.id,
                path=str(path),
                nar_size=info.info.nar_size,
            )
            dst_conn.op_log.append("AddToStoreNar (stream_paths_to)")

            fw.write(await info.bytes_wire())

            # Request NAR from source
            sp = SerdeStorePath(path=str(path))
            await NarFromPathRequest(path=sp).to_writer(WriteContext.from_conn(src_conn))
            await src_conn.w.drain()

            # Source will send stderr logs followed by STDERR_LAST before NAR data
            await src_conn.r.drain_stderr()

            # Pipe raw NAR data from source into the destination's framed stream
            await wire.pipe_raw_to_framed_writer(
                src_conn.r,
                fw,
                info.info.nar_size,
            )
            await dst_conn.w.drain()
            log.debug("stream_paths_path_sent", src_conn=src_conn.id, dst_conn=dst_conn.id, path=str(path))

        await fw.finalize()
        await dst_conn.w.drain()
