"""Dependency fan-out goals."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .goal import ExecutionGoal, Goal
from .results import GoalResult

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .engine import GoalEngine


@dataclass
class DependencyGroupGoal(ExecutionGoal[list[GoalResult]]):
    """Execute a set of child dependency goals concurrently."""

    engine: GoalEngine
    children: Sequence[Goal[GoalResult]]

    def __post_init__(self) -> None:
        """Initialize the ExecutionGoal base with the shared engine."""
        ExecutionGoal.__init__(self, self.engine)

    async def _run(self) -> list[GoalResult]:
        """Run all child dependency goals concurrently and return their results."""
        return await self.run_children(self.children)
