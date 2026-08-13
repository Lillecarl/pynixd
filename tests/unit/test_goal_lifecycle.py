"""Unit tests for shared goal lifecycle helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

import pytest

from pynixd.goals.goal import ExecutionGoal, Goal

if TYPE_CHECKING:
    from pynixd.goals.engine import GoalEngine


@dataclass
class StaticGoal[T](Goal[T]):
    engine: GoalEngine
    value: T

    def __post_init__(self) -> None:
        Goal.__init__(self, self.engine)

    async def _run(self) -> T:
        return self.value


@dataclass
class ParentGoal(ExecutionGoal[list[str | None]]):
    engine: GoalEngine
    children: list[Goal[str | None]]

    def __post_init__(self) -> None:
        ExecutionGoal.__init__(self, self.engine)

    async def _run(self) -> list[str | None]:
        return await self.run_children(self.children)


@pytest.mark.anyio
async def test_execution_goal_preserves_none_child_results() -> None:
    engine = cast("GoalEngine", None)
    parent = ParentGoal(
        engine=engine,
        children=[
            StaticGoal(engine, None),
            StaticGoal(engine, "done"),
        ],
    )

    assert await parent.result() == [None, "done"]
