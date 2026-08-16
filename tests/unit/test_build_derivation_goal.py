"""Unit tests for BuildDerivationGoal lifecycle behavior."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import anyio
import pytest

from pynixd.connection import ClientConn
from pynixd.goals.build_derivation import BuildDerivationGoal
from pynixd.serde import (
    BasicDerivation,
    BuildDerivationRequest,
    BuildDerivationResponse,
    BuildMode,
    BuildResult,
    BuildResultStatus,
    StorePath as SerdeStorePath,
)
from pynixd.serde.ids import BuildId
from pynixd.wire import BytesWriter

if TYPE_CHECKING:
    from pynixd.context import PynixdContext
    from pynixd.goals.engine import GoalEngine


class FakeScheduler:
    def __init__(self) -> None:
        self.started = anyio.Event()
        self.future: asyncio.Future[BuildDerivationResponse] | None = None

    async def build_derivation(
        self,
        request: BuildDerivationRequest,
        *,
        from_goal_path: bool = False,
    ) -> tuple[BuildId, asyncio.Future[BuildDerivationResponse]]:
        del request
        if not from_goal_path:
            raise RuntimeError("BuildDerivationGoal must mark scheduler builds as goal-owned")
        self.future = asyncio.get_running_loop().create_future()
        self.started.set()
        return BuildId(1), self.future


class FakeEngine:
    def __init__(self, scheduler: FakeScheduler) -> None:
        self.ctx = cast(
            "PynixdContext",
            SimpleNamespace(
                scheduler=scheduler,
                local_store=SimpleNamespace(),
            ),
        )
        self.subscribed: list[tuple[BuildId, ClientConn]] = []
        self.unsubscribed: list[tuple[BuildId, ClientConn]] = []

    async def subscribe_build(self, build_id: BuildId, client: ClientConn) -> bool:
        self.subscribed.append((build_id, client))
        return True

    async def unsubscribe_build(self, build_id: BuildId, client: ClientConn) -> None:
        self.unsubscribed.append((build_id, client))


def _request() -> BuildDerivationRequest:
    return BuildDerivationRequest(
        drv_path=SerdeStorePath(path="/nix/store/00000000000000000000000000000001-test.drv"),
        derivation=BasicDerivation(platform="x86_64-linux", builder=""),
        build_mode=BuildMode.NORMAL,
    )


@pytest.mark.anyio
async def test_build_derivation_goal_unsubscribes_late_subscriber() -> None:
    scheduler = FakeScheduler()
    engine = FakeEngine(scheduler)
    goal = BuildDerivationGoal(cast("GoalEngine", engine), _request())
    client = ClientConn(BytesWriter("client"))

    task = asyncio.create_task(goal.result())
    await scheduler.started.wait()
    await goal.subscribe(client)

    future = scheduler.future
    if future is None:
        raise RuntimeError("scheduler did not create a build future")
    future.set_result(
        BuildDerivationResponse(
            result=BuildResult(status=BuildResultStatus.BUILT),
        )
    )
    await task

    assert engine.subscribed == [(BuildId(1), client)]
    assert engine.unsubscribed == [(BuildId(1), client)]
