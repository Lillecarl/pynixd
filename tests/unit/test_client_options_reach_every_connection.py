"""An option of a client reaches every connection that works for that client.

`nix-daemon` holds one connection for one client, so `SetOptions` reaches
every operation of that client. pynixd holds a pool, and it sent the request
over the pool, so the option landed on whichever connection was free. A client
that passed `--post-build-hook` then saw the hook run for three of the five
derivations that its request built, because pynixd built the five at the same
time on several connections.

Issue #192 holds the measurement, and `scratchpad/probe_posthook.py` is the
program that made it.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

import anyio
import pytest

from pynixd.connection import ClientConn, Connection
from pynixd.goals.build_derivation import BuildDerivationGoal
from pynixd.handlers.set_options import SetOptionsHandler
from pynixd.serde import (
    BasicDerivation,
    BuildDerivationRequest,
    BuildDerivationResponse,
    BuildMode,
    BuildResult,
    BuildResultStatus,
    SetOptionsRequest,
    StorePath as SerdeStorePath,
)
from pynixd.serde.auth import Role
from pynixd.serde.context import WriteContext
from pynixd.serde.ids import BuildId
from pynixd.wire import PROTOCOL_VERSION, STDERR_LAST, BytesReader, BytesWriter

if TYPE_CHECKING:
    from pynixd.goals.engine import GoalEngine

VERSION = PROTOCOL_VERSION


def _options(hook: str) -> SetOptionsRequest:
    """Every field of the request, because none of them has a default."""
    return SetOptionsRequest(
        keep_failed=0,
        keep_going=0,
        try_fallback=0,
        verbosity=0,
        max_build_jobs=1,
        max_silent_time=0,
        obsolete_use_build_hook=0,
        build_verbosity=0,
        obsolete_log_type=0,
        obsolete_print_build_trace=0,
        build_cores=1,
        use_substitutes=1,
        overrides={"post-build-hook": hook},
    )


def _last() -> bytes:
    """The whole answer of the daemon to `SetOptions`: an empty log stream."""
    writer = BytesWriter("upstream")
    writer.write_uint64(STDERR_LAST)
    return writer.get_bytes()


async def _connection(answers: int) -> Connection:
    conn = Connection(
        BytesReader(_last() * answers, identifier="upstream"),
        BytesWriter("upstream"),
        "upstream",
    )
    conn.connected = True
    conn.version = VERSION
    return conn


@pytest.mark.anyio
async def test_a_connection_takes_the_option_set_of_the_client() -> None:
    conn = await _connection(1)

    await conn.apply_options(_options("/hook.sh"))

    assert conn.op_log == ["SetOptionsRequest"]
    assert conn.applied_options == _options("/hook.sh")


@pytest.mark.anyio
async def test_a_connection_that_carries_the_set_already_sends_nothing() -> None:
    """The common case is one client and one set, so it must cost one comparison."""
    conn = await _connection(1)
    await conn.apply_options(_options("/hook.sh"))

    await conn.apply_options(_options("/hook.sh"))

    assert conn.op_log == ["SetOptionsRequest"]


@pytest.mark.anyio
async def test_a_second_client_replaces_the_set() -> None:
    conn = await _connection(2)
    await conn.apply_options(_options("/first.sh"))

    await conn.apply_options(_options("/second.sh"))

    assert conn.op_log == ["SetOptionsRequest", "SetOptionsRequest"]
    assert conn.applied_options == _options("/second.sh")


@pytest.mark.anyio
async def test_no_option_set_leaves_the_connection_as_it_is() -> None:
    conn = await _connection(1)

    await conn.apply_options(None)

    assert conn.op_log == []


# ── The handler keeps the set, and sends nothing upstream ────────────


class FakeStore:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def call(self, request: Any) -> str:
        self.calls.append(type(request).__name__)
        return "answered"


class FakeProxy:
    def __init__(self, body: bytes) -> None:
        self.r = BytesReader(body, identifier="test:set-options")
        self.version = VERSION
        self.local_store = FakeStore()
        self.client = ClientConn(BytesWriter("client"))


@dataclass
class FakeContext:
    proxy: FakeProxy
    role: Role
    version: int = VERSION
    username: str = "test"


async def _body(request: SetOptionsRequest) -> bytes:
    """The request as the dispatcher hands it over: no operation number."""
    writer = BytesWriter("test")
    await request.to_writer(WriteContext(writer=writer, version=VERSION))
    return writer.get_bytes()[8:]


@pytest.mark.anyio
async def test_the_handler_keeps_the_set_on_the_session() -> None:
    ctx = FakeContext(FakeProxy(await _body(_options("/hook.sh"))), Role.ADMIN)

    await SetOptionsHandler().handle(cast("Any", ctx))

    assert ctx.proxy.client.options == _options("/hook.sh")


@pytest.mark.anyio
async def test_the_handler_sends_nothing_over_the_pool() -> None:
    """A connection of the pool is not the connection that does the work."""
    ctx = FakeContext(FakeProxy(await _body(_options("/hook.sh"))), Role.ADMIN)

    await SetOptionsHandler().handle(cast("Any", ctx))

    assert ctx.proxy.local_store.calls == []


@pytest.mark.anyio
async def test_a_user_that_is_not_an_admin_keeps_no_set() -> None:
    ctx = FakeContext(FakeProxy(await _body(_options("/hook.sh"))), Role.USER)

    await SetOptionsHandler().handle(cast("Any", ctx))

    assert ctx.proxy.client.options is None


# ── The build carries the set of the client that asked for it ────────


class FakeScheduler:
    def __init__(self) -> None:
        self.options: SetOptionsRequest | None = None
        self.future: asyncio.Future[BuildDerivationResponse] | None = None
        self.started = anyio.Event()

    async def build_derivation(
        self,
        request: BuildDerivationRequest,
        from_goal_path: bool = False,
        options: SetOptionsRequest | None = None,
    ) -> tuple[BuildId, asyncio.Future[BuildDerivationResponse]]:
        del request, from_goal_path
        self.options = options
        self.future = asyncio.get_running_loop().create_future()
        self.started.set()
        return BuildId(1), self.future


class FakeCtx:
    def __init__(self, scheduler: FakeScheduler) -> None:
        self.scheduler = scheduler


class FakeEngine:
    def __init__(self, scheduler: FakeScheduler) -> None:
        self.ctx = FakeCtx(scheduler)

    async def subscribe_build(self, build_id: BuildId, client: ClientConn) -> bool:
        del build_id, client
        return True

    async def unsubscribe_build(self, build_id: BuildId, client: ClientConn) -> None:
        del build_id, client


def _build_request() -> BuildDerivationRequest:
    return BuildDerivationRequest(
        drv_path=SerdeStorePath(path="/nix/store/00000000000000000000000000000001-test.drv"),
        derivation=BasicDerivation(platform="x86_64-linux", builder=""),
        build_mode=BuildMode.NORMAL,
    )


@pytest.mark.anyio
async def test_the_build_carries_the_set_of_the_client_that_asked() -> None:
    """A build runs after the request returned, so the queue keeps the set."""
    scheduler = FakeScheduler()
    goal = BuildDerivationGoal(cast("GoalEngine", FakeEngine(scheduler)), _build_request())
    client = ClientConn(BytesWriter("client"))
    client.options = _options("/hook.sh")
    await goal.subscribe(client)

    task = asyncio.create_task(goal.result())
    await scheduler.started.wait()
    future = scheduler.future
    if future is None:
        raise RuntimeError("the scheduler made no future")
    future.set_result(BuildDerivationResponse(result=BuildResult(status=BuildResultStatus.BUILT)))
    await task

    assert scheduler.options == _options("/hook.sh")
