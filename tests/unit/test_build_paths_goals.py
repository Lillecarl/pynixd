"""Unit tests for build request root goals."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import anyio
import pytest

from pynixd.goals.engine import GoalEngine
from pynixd.goals.goal import Goal
from pynixd.goals.requests import BuildPathsWithResultsGoal
from pynixd.goals.results import GoalResult, goal_failure, goal_success
from pynixd.serde import (
    BuildMode,
    BuildPathsRequest,
    BuildPathsWithResultsRequest,
    BuildPathsWithResultsResponse,
    BuildResultStatus,
    DerivedPath as SerdeDerivedPath,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from pynixd.connection import ClientConn
    from pynixd.derived_path import DerivedPath


class FakeEnsureGoal(Goal[GoalResult]):
    def __init__(
        self,
        engine: GoalEngine,
        result: GoalResult,
        *,
        before_result: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        super().__init__(engine)
        self._result = result
        self._before_result = before_result
        self.subscribers: list[ClientConn] = []

    async def subscribe(self, client: ClientConn | None) -> None:
        if client is not None:
            self.subscribers.append(client)

    async def _run(self) -> GoalResult:
        if self._before_result is not None:
            await self._before_result()
        return self._result


class FakeEngine:
    def __init__(self, goals: dict[str, FakeEnsureGoal]) -> None:
        self.goals = goals

    def substituter_ids(self) -> tuple[str, ...]:
        return ()

    async def get_ensure_derived_path_goal(
        self,
        path: DerivedPath,
        build_mode: int,
        substituter_ids: tuple[str, ...],
    ) -> FakeEnsureGoal:
        del build_mode, substituter_ids
        return self.goals[str(path)]

    async def build_paths_with_results(
        self,
        request: BuildPathsWithResultsRequest,
        client: ClientConn | None = None,
    ) -> BuildPathsWithResultsResponse:
        return await BuildPathsWithResultsGoal(cast("GoalEngine", self), request, client).result()


def _serde_path(path: str) -> Any:
    return SerdeDerivedPath(value=path)


def _request(paths: set[str]) -> BuildPathsWithResultsRequest:
    derived_paths: set[Any] = {_serde_path(path) for path in paths}
    return BuildPathsWithResultsRequest(
        derived_paths=cast("set[SerdeDerivedPath]", derived_paths),
        build_mode=BuildMode.NORMAL,
    )


@pytest.mark.anyio
async def test_build_paths_with_results_keeps_successes_when_one_root_fails() -> None:
    success_path = "/nix/store/11111111111111111111111111111111-success.drv!out"
    failure_path = "/nix/store/22222222222222222222222222222222-failure.drv!out"
    engine = FakeEngine(
        {
            success_path: FakeEnsureGoal(cast("GoalEngine", None), goal_success()),
            failure_path: FakeEnsureGoal(
                cast("GoalEngine", None),
                goal_failure("pynixd: expected test failure", BuildResultStatus.UNKNOWN),
            ),
        }
    )
    for goal in engine.goals.values():
        goal.engine = cast("GoalEngine", engine)

    response = await BuildPathsWithResultsGoal(
        cast("GoalEngine", engine),
        _request({success_path, failure_path}),
    ).result()

    results_by_path = {str(item.path): item.result for item in response.results}
    assert set(results_by_path) == {success_path, failure_path}
    assert BuildResultStatus(results_by_path[success_path].status).is_success
    assert BuildResultStatus(results_by_path[failure_path].status) == BuildResultStatus.UNKNOWN


@pytest.mark.anyio
async def test_build_paths_reports_failure_when_any_root_fails() -> None:
    success_path = "/nix/store/11111111111111111111111111111111-success.drv!out"
    failure_path = "/nix/store/22222222222222222222222222222222-failure.drv!out"
    engine = FakeEngine(
        {
            success_path: FakeEnsureGoal(cast("GoalEngine", None), goal_success()),
            failure_path: FakeEnsureGoal(
                cast("GoalEngine", None),
                goal_failure("pynixd: expected test failure", BuildResultStatus.UNKNOWN),
            ),
        }
    )
    for goal in engine.goals.values():
        goal.engine = cast("GoalEngine", engine)

    derived_paths: set[Any] = {_serde_path(success_path), _serde_path(failure_path)}
    response = await GoalEngine.build_paths(
        cast("GoalEngine", engine),
        BuildPathsRequest(
            derived_paths=cast("set[SerdeDerivedPath]", derived_paths),
            build_mode=BuildMode.NORMAL,
        ),
    )

    assert response.value == 1


@pytest.mark.anyio
async def test_build_paths_with_results_runs_root_goals_in_parallel() -> None:
    first_path = "/nix/store/11111111111111111111111111111111-first.drv!out"
    second_path = "/nix/store/22222222222222222222222222222222-second.drv!out"
    second_started = anyio.Event()

    async def wait_for_second() -> None:
        await second_started.wait()

    async def mark_second_started() -> None:
        second_started.set()

    engine = FakeEngine(
        {
            first_path: FakeEnsureGoal(
                cast("GoalEngine", None),
                goal_success(),
                before_result=wait_for_second,
            ),
            second_path: FakeEnsureGoal(
                cast("GoalEngine", None),
                goal_success(),
                before_result=mark_second_started,
            ),
        }
    )
    for goal in engine.goals.values():
        goal.engine = cast("GoalEngine", engine)

    with anyio.fail_after(1):
        response = await BuildPathsWithResultsGoal(
            cast("GoalEngine", engine),
            _request({first_path, second_path}),
        ).result()

    assert {str(item.path) for item in response.results} == {first_path, second_path}
