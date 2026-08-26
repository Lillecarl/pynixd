"""BuildDerivation action goal."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar

import anyio
import structlog

from ..serde import (
    BuildDerivationRequest,
    BuildResultStatus,
    IsValidPathRequest,
    StorePath as SerdeStorePath,
)
from ..store_path import StorePath
from .goal import ExecutionGoal
from .results import GoalResult, goal_failure, result_succeeded

if TYPE_CHECKING:
    from ..connection import ClientConn
    from ..serde.ids import BuildId
    from .engine import GoalEngine

log = structlog.get_logger(__name__)


@dataclass
class BuildDerivationGoal(ExecutionGoal[GoalResult]):
    """Execute a single derivation build via the scheduler's build queue."""

    may_reach_a_root_goal: ClassVar[bool] = False
    """A build goal reaches no root goal, so a caller may wait and keep its place.

    `_run_and_let_the_next_goal_enqueue` calls the scheduler and nothing else
    before the build is on the queue. `Goal.may_reach_a_root_goal` gives the
    rule. Issue #207.
    """

    engine: GoalEngine
    request: BuildDerivationRequest
    _subscribers: list[ClientConn] = field(default_factory=list)
    _active_subscribers: list[ClientConn] = field(default_factory=list)
    _build_id: BuildId | None = None
    _finished: bool = False
    _reached_the_queue: anyio.Event = field(default_factory=anyio.Event)
    """Set when the build is on the queue, or when this goal ends without one.

    A root goal of a request waits for this before it lets the next root goal
    enqueue. The end of the build is far later, and the order needs the
    earlier moment. Issue #207.
    """

    def __post_init__(self) -> None:
        """Initialize the ExecutionGoal base with the shared engine."""
        ExecutionGoal.__init__(self, self.engine)

    async def wait_until_it_reached_the_queue(self) -> None:
        """Return when the build is on the queue, or when this goal ended."""
        await self._reached_the_queue.wait()

    async def subscribe(self, client: ClientConn | None) -> None:
        """Register a client for real-time log forwarding during the build."""
        if client is None:
            return
        async with self._lock:
            if self._finished:
                return
            build_id = self._build_id
            if build_id is None:
                self._subscribers.append(client)
                return
        await self._subscribe_active(build_id, client)

    async def unsubscribe(self, client: ClientConn | None) -> None:
        """Stop sending the log of this build to *client*.

        The build runs on for every other client that asked for it. This says
        one client stopped listening, and nothing more. Issue #196.
        """
        if client is None:
            return
        async with self._lock:
            build_id = self._build_id
            while client in self._subscribers:
                self._subscribers.remove(client)
            while client in self._active_subscribers:
                self._active_subscribers.remove(client)
        if build_id is not None:
            await self.engine.unsubscribe_build(build_id, client)

    async def _subscribe_active(self, build_id: BuildId, client: ClientConn) -> None:
        if not await self.engine.subscribe_build(build_id, client):
            return

        should_unsubscribe = False
        async with self._lock:
            if self._finished or self._build_id != build_id:
                should_unsubscribe = True
            else:
                self._active_subscribers.append(client)

        if should_unsubscribe:
            await self.engine.unsubscribe_build(build_id, client)

    async def _run(self) -> GoalResult:
        # **Every road out of this method must let the next goal enqueue.**
        # A goal that fails before it reaches the queue would otherwise hold
        # every root goal after it in the order. Issue #207.
        try:
            return await self._run_and_let_the_next_goal_enqueue()
        finally:
            self._reached_the_queue.set()

    async def _run_and_let_the_next_goal_enqueue(self) -> GoalResult:
        if self.engine.ctx.scheduler is None:
            return goal_failure("pynixd: BuildDerivation requires a configured scheduler")

        # **The build carries the options of the client that asked for it.**
        # A build runs after the request of the client returned, so the
        # connection that runs it must get the set from the queue. Issue #192.
        options = next((c.options for c in self._subscribers if c.options is not None), None)
        build_id, future = await self.engine.ctx.scheduler.build_derivation(
            self.request,
            from_goal_path=True,
            options=options,
        )
        # `from_goal_path` made the queue take a reference for this request,
        # under its own lock. This records which one to give back when the
        # request answers, and it takes no await, so nothing lands between the
        # two calls and loses the record. Issue #196.
        self.engine.note_a_held_build(build_id)
        # The build is on the queue now, so the next root goal of the request
        # may enqueue behind it and the order of the queue decides. Issue #207.
        self._reached_the_queue.set()
        async with self._lock:
            self._build_id = build_id
            subscribers = list(self._subscribers)
            self._subscribers.clear()

        for client in subscribers:
            await self._subscribe_active(build_id, client)

        try:
            response = await future
        finally:
            async with self._lock:
                self._finished = True
                active_subscribers = list(self._active_subscribers)
                self._active_subscribers.clear()
            for client in active_subscribers:
                await self.engine.unsubscribe_build(build_id, client)

        resolved: dict[str, StorePath] = {}
        produced: set[StorePath] = set()

        for name, output in self.request.derivation.outputs.items():
            if output.path:
                path = StorePath(output.path)
                resolved[name] = path
                produced.add(path)

        # `realised_outputs` keys by the output name, whichever of the two
        # wire shapes the answer carried. Issue #162.
        for output_name, realisation in response.result.realised_outputs().items():
            if realisation.out_path:
                path = StorePath(str(realisation.out_path)).with_store_prefix()
                resolved[output_name] = path
                produced.add(path)

        # **A build that failed produced nothing, so it waits for nothing.**
        # `produced` holds the path that the derivation *declares*, which the
        # loop above fills before anything reads the status, so the set is
        # never empty and the wait always ran. A failure means that path
        # cannot appear, so the wait ended at its deadline every time.
        # Measured: 2.0498 s for each failed build of
        # `nix build -f fod-failing.nix -j1 -L`, holding 226 `IsValidPath`
        # queries and nothing else. Issue #287.
        if result_succeeded(response.result):
            await self._wait_for_local_paths(produced)

        status = response.result.status
        if not produced and status == BuildResultStatus.BUILT:
            for path in self.request.derivation.output_paths().values():
                if path:
                    produced.add(path)

        return GoalResult(
            result=response.result,
            resolved_outputs=resolved,
            produced_paths=produced,
        )

    async def _wait_for_local_paths(self, paths: set[StorePath]) -> None:
        if not paths:
            return
        deadline = anyio.current_time() + 2.0
        while anyio.current_time() < deadline:
            valid_paths = [await self._is_valid_local_path(path) for path in paths]
            if all(valid_paths):
                return
            await anyio.sleep(0.05)

    async def _is_valid_local_path(self, path: StorePath) -> bool:
        response = await self.engine.ctx.local_store.execute(IsValidPathRequest(path=SerdeStorePath(path=str(path))))
        return bool(response.valid)
