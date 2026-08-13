"""Shared goal lifecycle primitives."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, TypeVar, cast

import anyio

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .engine import GoalEngine


T = TypeVar("T")
U = TypeVar("U")
_MISSING = object()


class Goal[T]:
    """A deduped async computation shared by multiple request waiters."""

    def __init__(self, engine: GoalEngine) -> None:
        self.engine = engine
        self._lock = anyio.Lock()
        self._task: asyncio.Task[T] | None = None

    async def result(self) -> T:
        """Await the goal's result, starting execution on first call."""
        async with self._lock:
            if self._task is None:
                self._task = asyncio.create_task(self._run())
            task = self._task
        return await task

    async def _run(self) -> T:
        """Execute the goal's core logic. Must be overridden by subclasses."""
        raise NotImplementedError


class GoalHolder(Goal[T]):
    """A coordinator goal that advances through child goals serially."""

    async def run_child(self, child: Goal[U]) -> U:
        """Run a single child goal and return its result."""
        return await child.result()

    async def run_children(self, children: Sequence[Goal[U]]) -> list[U]:
        """Run child goals serially and return their results in order."""
        return [await child.result() for child in children]


class ExecutionGoal(Goal[T]):
    """A fan-out goal that waits for child goals in parallel."""

    async def run_children(self, children: Sequence[Goal[U]]) -> list[U]:
        """Run child goals concurrently and return their results in order."""
        results: list[U | object] = [_MISSING] * len(children)

        async def run_one(index: int, child: Goal[U]) -> None:
            results[index] = await child.result()

        async with anyio.create_task_group() as tg:
            for index, child in enumerate(children):
                tg.start_soon(run_one, index, child)

        collected: list[U] = []
        for result in results:
            if result is _MISSING:
                raise RuntimeError("goal child did not record a result")
            collected.append(cast("U", result))
        return collected
