"""The first failure stops the request, and `max-jobs` says how many run.

`Worker::removeGoal` at `worker.cc:173` clears `topGoals` when a top goal
fails and `keepGoing` is off, and `Worker::run` then leaves its loop.
`Worker::buildPathsWithResults` at `entry-points.cc:93` skips each goal whose
`exitCode` is still `ecBusy`, so the answer holds fewer entries than the
request. Issue #190.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import anyio
import pytest

from pynixd.derived_path import DerivedPath
from pynixd.goals.requests import BuildPathsWithResultsGoal
from pynixd.goals.results import GoalResult, goal_failure, goal_success
from pynixd.serde import (
    BuildMode,
    BuildPathsWithResultsRequest,
    DerivedPath as SerdeDerivedPath,
    SetOptionsRequest,
)
from pynixd.serde.ids import LOCAL_STORE_ID, StoreId

if TYPE_CHECKING:
    from pynixd.connection import ClientConn
    from pynixd.goals.engine import GoalEngine

_PATHS = [f"/nix/store/{str(index) * 32}-x{index}.drv!out" for index in (1, 2, 3, 4)]


def _options(*, max_build_jobs: int, keep_going: bool) -> SetOptionsRequest:
    """A `SetOptions` request with every field, because no field has a default."""
    return SetOptionsRequest(
        keep_failed=False,
        keep_going=keep_going,
        try_fallback=False,
        verbosity=0,
        max_build_jobs=max_build_jobs,
        max_silent_time=0,
        obsolete_use_build_hook=True,
        build_verbosity=0,
        obsolete_log_type=0,
        obsolete_print_build_trace=0,
        build_cores=1,
        use_substitutes=True,
        overrides={},
    )


class FakeEnsureGoal:
    """A goal that records that it ran, and answers success or failure."""

    def __init__(self, name: str, *, succeeds: bool, started: list[str]) -> None:
        self.name = name
        self.succeeds = succeeds
        self.started = started

    async def subscribe(self, client: Any) -> None:
        del client

    async def result(self) -> GoalResult:
        self.started.append(self.name)
        # One checkpoint, so a task that holds the only slot really holds it.
        await anyio.sleep(0)
        return goal_success() if self.succeeds else goal_failure(f"{self.name} failed")


class FakeEngine:
    def __init__(self, goals: dict[str, FakeEnsureGoal], *, has_a_backend: bool) -> None:
        self.goals = goals
        stores: dict[StoreId, object] = {LOCAL_STORE_ID: object()}
        if has_a_backend:
            stores[StoreId("builder")] = object()
        self.ctx = SimpleNamespace(stores=stores)

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


def _client(options: SetOptionsRequest | None) -> ClientConn:
    return cast("ClientConn", SimpleNamespace(options=options))


async def _run(
    *,
    failing: set[str],
    options: SetOptionsRequest | None,
    has_a_backend: bool = False,
) -> tuple[list[str], list[str]]:
    """Run the four paths, and answer which goals ran and which got an answer."""
    started: list[str] = []
    goals = {path: FakeEnsureGoal(path, succeeds=path not in failing, started=started) for path in _PATHS}
    engine = FakeEngine(goals, has_a_backend=has_a_backend)
    request = BuildPathsWithResultsRequest(
        derived_paths=[SerdeDerivedPath(value=path) for path in _PATHS],
        build_mode=BuildMode.NORMAL,
    )

    response = await BuildPathsWithResultsGoal(
        cast("GoalEngine", engine),
        request,
        _client(options),
    ).result()

    return started, [str(item.path) for item in response.results]


@pytest.mark.anyio
async def test_one_slot_runs_the_goals_in_the_order_of_the_request() -> None:
    """`-j1` against a pynixd with no backend runs one goal at a time."""
    started, answered = await _run(failing=set(), options=_options(max_build_jobs=1, keep_going=False))

    assert started == _PATHS
    assert answered == _PATHS


@pytest.mark.anyio
async def test_the_first_failure_stops_the_request() -> None:
    """x1 fails, so x2, x3 and x4 never run and report nothing."""
    started, answered = await _run(
        failing={_PATHS[0]},
        options=_options(max_build_jobs=1, keep_going=False),
    )

    assert started == [_PATHS[0]]
    assert answered == [_PATHS[0]]


@pytest.mark.anyio
async def test_keep_going_runs_every_goal_after_a_failure() -> None:
    started, answered = await _run(
        failing={_PATHS[0]},
        options=_options(max_build_jobs=1, keep_going=True),
    )

    assert started == _PATHS
    assert answered == _PATHS


@pytest.mark.anyio
async def test_a_backend_keeps_the_fan_out_that_the_client_asked_to_limit() -> None:
    """`max-jobs` limits the local builds alone, as `worker.cc:261` does.

    A remote build costs no slot in Nix, and pynixd sends a build to a backend
    when it has one. So `-j1` must not serialise a pynixd with a backend.
    """
    started, answered = await _run(
        failing=set(),
        options=_options(max_build_jobs=1, keep_going=False),
        has_a_backend=True,
    )

    assert sorted(started) == sorted(_PATHS)
    assert answered == _PATHS


@pytest.mark.anyio
async def test_max_jobs_of_zero_still_runs_one_goal() -> None:
    """`max-jobs = 0` means "no build here", and the goal system has nowhere else."""
    started, _ = await _run(failing=set(), options=_options(max_build_jobs=0, keep_going=False))

    assert started == _PATHS


@pytest.mark.anyio
async def test_a_client_with_no_option_set_keeps_the_fan_out() -> None:
    """A client that sent no `SetOptions` reads the behaviour of before."""
    started, answered = await _run(failing=set(), options=None)

    assert sorted(started) == sorted(_PATHS)
    assert answered == _PATHS
