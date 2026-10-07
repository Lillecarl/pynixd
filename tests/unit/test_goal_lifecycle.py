"""Unit tests for shared goal lifecycle helpers."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

import anyio
import pytest

from pynixd.goals.engine import GoalEngine
from pynixd.goals.goal import ExecutionGoal, Goal

if TYPE_CHECKING:
    from pynixd.context import PynixdContext


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


@dataclass
class HangingGoal(Goal[None]):
    engine: GoalEngine

    def __post_init__(self) -> None:
        Goal.__init__(self, self.engine)

    async def _run(self) -> None:
        await anyio.sleep(60.0)


def _engine_with(*goals: Goal[Any]) -> GoalEngine:
    """A real engine holding the given goals. Nothing touches the context."""
    engine = GoalEngine(cast("PynixdContext", None))
    for index, goal in enumerate(goals):
        engine._goals[index] = goal
    return engine


@pytest.mark.anyio
async def test_reap_cancels_an_unfinished_goal() -> None:
    goal = HangingGoal(cast("GoalEngine", None))
    engine = _engine_with(goal)
    await goal.start()
    assert engine.reap() == 1
    assert goal._task is not None
    with contextlib.suppress(asyncio.CancelledError):
        await goal._task
    assert goal._task.cancelled()


@pytest.mark.anyio
async def test_reap_leaves_a_finished_goal_alone() -> None:
    engine = cast("GoalEngine", None)
    goal = StaticGoal(engine, "done")
    holder = _engine_with(goal)
    assert await goal.result() == "done"
    assert holder.reap() == 0
    assert await goal.result() == "done"


@pytest.mark.anyio
async def test_reap_counts_only_the_unfinished() -> None:
    engine = cast("GoalEngine", None)
    finished = StaticGoal(engine, "done")
    holder = _engine_with(HangingGoal(cast("GoalEngine", None)), finished, HangingGoal(cast("GoalEngine", None)))
    assert await finished.result() == "done"
    for goal in holder._goals.values():
        await goal.start()
    assert holder.reap() == 2
