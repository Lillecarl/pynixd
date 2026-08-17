"""
Nix daemon protocol session handler.

Accepts a client connection, performs the handshake, decodes operations,
and dispatches them to request type handle() classmethods.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, cast

import asyncssh
import structlog

from nix_daemon_protocol.exceptions import DaemonProtocolError

from . import wire
from .config import ScheduleMode
from .connection import ClientConn
from .exceptions import OpNotImplementedError, PynixdError
from .goals import GoalEngine
from .handlers._base import HANDLER_REGISTRY
from .protocol import get_extension_features
from .serde import (
    BuildPathsRequest,
    BuildPathsWithResultsRequest,
    IsValidPathRequest,
    IsValidPathResponse,
    LogError,
    QueryMissingRequest,
    QueryPathInfoRequest,
    QueryValidPathsRequest,
    QueryValidPathsResponse,
)
from .serde.auth import Role
from .serde.context import ReadContext, RequestContext as RequestContext, WriteContext
from .serde.ids import LOCAL_STORE_ID, StoreId
from .serde.protocol import OptTrusted, Verbosity
from .serde.wire_ops import WIRE_REGISTRY, WireResponse
from .store_layout import StoreLayout
from .temp_roots import TempRoots

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from .build_queue import BuildQueue
    from .context import PynixdContext
    from .scheduler import Scheduler
    from .serde.wire_ops import WireRequest
    from .store import LocalStore, Store
    from .wire import NixReader, NixWriter

from .store import DaemonStore as DaemonStore

log = structlog.get_logger(__name__)


def _error_text(ex: BaseException) -> str:
    """The text that `STDERR_ERROR` carries for *ex*.

    **A client prints this text after the word "error:", so it must read as
    one.** This was `repr(ex)`, which gave the client
    `error: BackendError("Cannot build '\\x1b[35;1m/nix/store/...")` -- the
    name of a Python class, a quoted string, and every escape of the message
    doubled. Nix writes the message alone.

    A `PynixdError` carries a message that pynixd wrote for a reader, and a
    `DaemonProtocolError` carries the message of a daemon behind pynixd. Both
    are the whole text. Any other exception is a fault of pynixd, and the name
    of the class is the part that says so.

    **A task group carries the failure of its task, and the group itself says
    nothing.** Every handler that fans out uses `anyio.create_task_group`, and
    a task that raises leaves an `ExceptionGroup`. The client then read
    `error: ExceptionGroup: unhandled errors in a TaskGroup (1 sub-exception)`
    and never learned the reason. `ca:signatures` reads the reason, which is
    "cannot add path '...' because it lacks a signature by a trusted key".
    """
    if isinstance(ex, BaseExceptionGroup):
        return "\n".join(_error_text(inner) for inner in ex.exceptions)
    if isinstance(ex, PynixdError | DaemonProtocolError):
        return str(ex)
    return f"{type(ex).__name__}: {ex}"


NIX_VERSION: str = "pynixd-0.1.0"


class DaemonProxy:
    """Per-client session: handshake, op dispatch, response encoding.

    Each client connection gets its own DaemonProxy. Connection instances
    are acquired from the local_store pool per-operation and returned
    immediately, keeping pool usage minimal.
    """

    def __init__(
        self,
        client_r: NixReader,
        client_w: NixWriter,
        ctx: PynixdContext,
        role: Role = Role.USER,
        username: str = "unknown",
        schedule_mode: ScheduleMode = ScheduleMode.auto,
    ) -> None:
        self.r = client_r
        self.w = client_w
        self.client = ClientConn(w=self.w)
        self.ctx = ctx
        self.version: int = wire.PROTOCOL_VERSION
        self.role: Role = role
        self.username: str = username
        self.schedule_mode: ScheduleMode = schedule_mode
        self._op_timing: dict[int, tuple[int, float]] = {}
        self._temp_roots: TempRoots | None = None

    @property
    def local_store(self) -> LocalStore:
        """The local Nix store for direct store operations."""
        return self.ctx.local_store

    @property
    def scheduler(self) -> Scheduler | None:
        """Build scheduler instance, or None if no remote stores."""
        return self.ctx.scheduler

    @property
    def build_queue(self) -> BuildQueue | None:
        """Build queue from the scheduler, or None if unconfigured."""
        return self.scheduler.queue if self.scheduler else None

    @property
    def goal_engine(self) -> GoalEngine:
        """Goal engine for client-facing build operations."""
        return GoalEngine(self.ctx)

    @property
    def scheduler_trigger(self) -> Callable[[], None] | None:
        """Callback to wake the scheduler, or None if unconfigured."""
        return self.scheduler.trigger if self.scheduler else None

    @property
    def stores(self) -> Mapping[StoreId, Store]:
        """All configured stores by store ID."""
        return self.ctx.stores

    @property
    def use_scheduler_for_builds(self) -> bool:
        """Whether builds should go through the scheduler queue.

        In 'auto' mode, uses scheduler when builder stores are configured.
        In 'scheduler' mode, always uses scheduler (builds queue even with
        no builders). In 'proxy' mode, never uses scheduler.
        """
        if self.schedule_mode == ScheduleMode.proxy:
            return False
        if self.schedule_mode == ScheduleMode.scheduler:
            return True
        # auto: scheduler if there are builder stores
        return self.scheduler is not None and bool(self.scheduler.stores)

    async def run(self) -> None:
        """Run the full session lifecycle."""
        try:
            await self.handshake()
            await self.op_loop()
        except (EOFError, BrokenPipeError, ConnectionError, OSError, asyncssh.misc.ConnectionLost):
            log.debug("client_disconnected")
        except Exception:
            log.exception("session_error")
        finally:
            if self._temp_roots is not None:
                await self._temp_roots.close()
            if self._op_timing:
                total_time = sum(t for _, t in self._op_timing.values())
                total_ops = sum(n for n, _ in self._op_timing.values())
                breakdown = {}
                for op_num, (count, acc_time) in sorted(self._op_timing.items()):
                    req_cls = WIRE_REGISTRY.get(op_num)
                    name = req_cls.name if req_cls else f"op_{op_num}"
                    breakdown[name] = f"x{count} {acc_time:.3f}s"
                log.info(
                    "client_op_timing",
                    total_ops=total_ops,
                    total_time=f"{total_time:.3f}s",
                    breakdown=breakdown,
                )

    # ── Handshake ────────────────────────────────────────────────────

    async def handshake(self) -> None:
        """Server-side daemon protocol handshake."""
        magic = await self.r.read_uint64()
        if magic != wire.WORKER_MAGIC_1:
            raise ValueError(f"Bad client magic: {magic:#x}")

        # Present pynixd's supported protocol version to the client.
        # This enables feature negotiation (1.38+) even if the local store is older.
        server_version = wire.PROTOCOL_VERSION
        self.w.write_uint64(wire.WORKER_MAGIC_2)
        self.w.write_uint64(server_version)
        await self.w.drain()

        client_version = await self.r.read_uint64()
        self.version = min(server_version, client_version)
        log.info(
            "client_protocol_negotiated",
            client_version=wire.proto_str(client_version),
            local_store_version=wire.proto_str(server_version),
            negotiated_version=wire.proto_str(self.version),
        )

        # Feature negotiation (1.38+) — before CPU/reserveSpace
        if self.version >= wire.proto(1, 38):
            client_features = await self.r.read_string_set()
            log.debug("client_features", client_features=client_features)

            our_features = get_extension_features()

            # Only build-capable stores contribute scheduling capabilities.
            # Substituters are deliberately non-scheduleable; advertising
            # their host features would incorrectly tell clients that this
            # server can build for those platforms.
            for store in self.stores.values():
                if store.no_schedule:
                    continue
                fm = store._feature_matrix
                if fm:
                    for system, features in fm.items():
                        our_features.add(f"feature_matrix:{system}")
                        for feat in features:
                            our_features.add(f"feature_matrix:{system}:{feat}")

            self.w.write_string_set(our_features)  # our features
            await self.w.drain()

        if await self.r.read_uint64():  # sendCpu
            await self.r.read_uint64()  # cpuAffinity (ignored)
        await self.r.read_uint64()  # reserveSpace (ignored)

        # Server conditions these on clientVersion
        if client_version >= wire.proto(1, 33):
            self.w.write_string(NIX_VERSION)
            if client_version >= wire.proto(1, 35):
                self.w.write_uint64(OptTrusted.TRUSTED)
        self.w.write_uint64(wire.STDERR_LAST)
        await self.w.drain()

        log.info("client_handshake_complete")

    # ── Op loop ──────────────────────────────────────────────────────

    async def op_loop(self) -> None:
        """Read ops, dispatch, write responses."""
        while True:
            try:
                op_num = await self.r.read_uint64()
            except (EOFError, asyncssh.misc.ConnectionLost):
                break

            req_cls = WIRE_REGISTRY.get(op_num)
            handler_cls = HANDLER_REGISTRY.get(op_num)
            if req_cls is None and handler_cls is None:
                log.warning("unknown_op", op_num=op_num)
                await self.send_error(f"Unsupported operation: {op_num}")
                continue

            op_name = req_cls.name if req_cls else handler_cls.__name__ if handler_cls else f"op_{op_num}"

            t0 = time.monotonic()
            try:
                response = await self.dispatch(op_num)

                if response is not None:
                    await self.client.flush()
                    await response.to_writer(WriteContext.from_proxy(self))
                    await self.w.drain()
                # else: already handled (streaming, error, etc.)

            except Exception as ex:
                log.exception("handle_op_error", name=op_name)
                await self.client.flush()
                await self.send_error(_error_text(ex))
            finally:
                elapsed = time.monotonic() - t0
                count, acc = self._op_timing.get(op_num, (0, 0.0))
                self._op_timing[op_num] = (count + 1, acc + elapsed)

    # ── Dispatch ─────────────────────────────────────────────────────

    async def execute(  # type: ignore[no-overload-impl]
        self,
        request: WireRequest,
    ) -> Any:
        """Execute an operation, falling back to other stores for extensions."""
        if isinstance(request, BuildPathsWithResultsRequest):
            return await self.goal_engine.build_paths_with_results(request, client=self.client)
        if isinstance(request, BuildPathsRequest):
            return await self.goal_engine.build_paths(request, client=self.client)
        if isinstance(request, QueryMissingRequest):
            return await self.goal_engine.query_missing(request, client=self.client)

        local_resp: WireResponse | None = None
        try:
            local_resp = await self.local_store.execute(request, client=self.client)
            mapped_resp = await self._mapped_output_response(request, local_resp)
            if mapped_resp is not None:
                return mapped_resp
            if local_resp is not None and not (request.is_extension and local_resp.is_not_found):
                return local_resp
        except OpNotImplementedError:
            if not request.is_extension:
                raise

        # Extension not supported by local store or returned not found — try other stores
        for store in self.stores.values():
            if store.store_id == LOCAL_STORE_ID:
                continue  # already tried above
            try:
                # We don't forward client logs to remote stores for simple queries
                # unless they are builds.
                resp = await store.execute(request, client=self.client)
                if not resp.is_not_found:
                    return resp
            except OpNotImplementedError:
                continue

        # If we got here, none of the backends had a "found" result.
        # Return the local_store result (even if empty) as the final word,
        # unless it wasn't even implemented.
        if local_resp is not None:
            return local_resp

        raise OpNotImplementedError(
            f"Extension operation {type(request).__name__} (op={request.op}) not supported by any configured store",
        )

    async def _mapped_output_response(
        self, request: WireRequest, local_resp: WireResponse | None
    ) -> WireResponse | None:
        if isinstance(request, QueryPathInfoRequest):
            if local_resp is not None and getattr(local_resp, "valid", False):
                return None
            store = self.store_for_output_path(str(request.path))
            if store is None:
                return None
            return cast("WireResponse", await store.execute(request, client=self.client))

        if isinstance(request, IsValidPathRequest):
            if local_resp is not None and getattr(local_resp, "valid", False):
                return None
            if self.store_for_output_path(str(request.path)) is None:
                return None
            return IsValidPathResponse(valid=True)

        if isinstance(request, QueryValidPathsRequest):
            paths = set(getattr(local_resp, "paths", set())) if local_resp is not None else set()
            for path in request.paths:
                if self.store_for_output_path(str(path)) is not None:
                    paths.add(path)
            if local_resp is not None and paths == getattr(local_resp, "paths", set()):
                return None
            return QueryValidPathsResponse(paths=paths)

        return None

    def store_for_output_path(self, path: str) -> DaemonStore | None:
        """Look up the DaemonStore that produced a given output path."""
        return self.ctx.store_for_output_path(path)

    async def dispatch(self, op_num: int) -> WireResponse | None:
        """Route an operation to its request type's handle method."""
        # NEW: try new handler registry first
        if handler_cls := HANDLER_REGISTRY.get(op_num):
            response = await handler_cls().handle(
                RequestContext(
                    proxy=self,
                    role=self.role,
                    version=self.version,
                    username=self.username,
                )
            )
            return cast(
                "WireResponse | None",
                response,
            )

        # NEW: try serde wire registry for handler-less ops
        if wire_cls := WIRE_REGISTRY.get(op_num):
            req = await wire_cls.from_reader(
                ReadContext(reader=self.r, version=self.version),
            )
            return await self.execute(req)

        log.warning("unhandled_op", op_num=op_num)
        await self.send_error(f"Unhandled operation: {op_num}")
        return None

    # ── Temporary roots ──────────────────────────────────────────────

    async def add_temp_root(self, path: str) -> None:
        """Hold `path` against the collector until this client goes away.

        pynixd writes the root itself, in the `temproots` directory of the
        store. It used to forward the operation to the upstream daemon, and
        the root then belonged to a pooled connection rather than to the
        client that asked for it. Issue #174, and `temp_roots.py` for how the
        file works.
        """
        if self._temp_roots is None:
            # The state directory of the store that pynixd serves, which is
            # `<root>/nix/var/nix` for a chroot store and `NIX_STATE_DIR` for
            # a relocated one. `StoreLayout` answers both. Issue #176.
            layout = getattr(self.local_store, "layout", None) or StoreLayout.chroot(None)
            self._temp_roots = TempRoots(layout.state_dir)
        await self._temp_roots.add(path)

    # ── Helpers ───────────────────────────────────────────────────────

    async def send_error(self, msg: str) -> None:
        """Send a STDERR_ERROR to the client."""
        await self.client.send(
            LogError(
                type="Error",
                level=Verbosity.ERROR,
                name="Error",
                msg=msg,
                have_pos=0,
                traces=[],
            )
        )
        await self.client.flush()
