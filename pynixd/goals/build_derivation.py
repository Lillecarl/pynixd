"""BuildDerivation action goal."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

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
from .results import GoalResult, goal_failure

if TYPE_CHECKING:
    from ..connection import ClientConn
    from ..serde.ids import BuildId
    from .engine import GoalEngine

log = structlog.get_logger(__name__)


@dataclass
class BuildDerivationGoal(ExecutionGoal[GoalResult]):
    """Execute a single derivation build via the scheduler's build queue."""

    engine: GoalEngine
    request: BuildDerivationRequest
    _subscribers: list[ClientConn] = field(default_factory=list)
    _active_subscribers: list[ClientConn] = field(default_factory=list)
    _build_id: BuildId | None = None
    _finished: bool = False

    def __post_init__(self) -> None:
        """Initialize the ExecutionGoal base with the shared engine."""
        ExecutionGoal.__init__(self, self.engine)

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
        if self.engine.ctx.scheduler is None:
            return goal_failure("pynixd: BuildDerivation requires a configured scheduler")

        build_id, future = await self.engine.ctx.scheduler.build_derivation(
            self.request,
            from_goal_path=True,
        )
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

        if response.result.built_outputs:
            for key, realisation in response.result.built_outputs.items():
                output_name = realisation.id.output_name or key.split("!", 1)[-1]
                if realisation.out_path:
                    path = StorePath(str(realisation.out_path)).with_store_prefix()
                    resolved[output_name] = path
                    produced.add(path)

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
