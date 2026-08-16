"""Request-root goals for daemon build operations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..derived_path import DerivedPath
from ..serde import (
    BuildPathsWithResultsRequest,
    BuildPathsWithResultsResponse,
    DerivedPath as SerdeDerivedPath,
    KeyedBuildResult,
)
from .goal import ExecutionGoal

if TYPE_CHECKING:
    from ..connection import ClientConn
    from .engine import GoalEngine
    from .ensure import EnsureDerivedPathGoal


@dataclass
class BuildPathsWithResultsGoal(ExecutionGoal[BuildPathsWithResultsResponse]):
    """Root goal for BuildPathsWithResults daemon requests.

    Translates each derived path into an EnsureDerivedPathGoal,
    subscribes the client for real-time log forwarding, and collects
    results into a BuildPathsWithResultsResponse.
    """

    engine: GoalEngine
    request: BuildPathsWithResultsRequest
    client: ClientConn | None = None
    _root_goals: list[tuple[SerdeDerivedPath, EnsureDerivedPathGoal]] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Initialize the ExecutionGoal base with the shared engine."""
        ExecutionGoal.__init__(self, self.engine)

    async def _run(self) -> BuildPathsWithResultsResponse:
        """Fan out to one EnsureDerivedPathGoal per derived path and collect results."""
        substituter_ids = self.engine.substituter_ids()
        for serde_path in sorted(self.request.derived_paths, key=str):
            path = DerivedPath(str(serde_path))
            goal = await self.engine.get_ensure_derived_path_goal(path, self.request.build_mode, substituter_ids)
            await goal.subscribe(self.client)
            self._root_goals.append((SerdeDerivedPath(value=str(path)), goal))

        root_results = await self.run_children([goal for _, goal in self._root_goals])
        return BuildPathsWithResultsResponse(
            results=[
                KeyedBuildResult(path=serde_path, result=result.result)
                for (serde_path, _goal), result in zip(self._root_goals, root_results, strict=True)
            ]
        )
