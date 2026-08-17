"""Request-root goals for daemon build operations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import anyio

from ..derived_path import DerivedPath
from ..serde import (
    BuildPathsWithResultsRequest,
    BuildPathsWithResultsResponse,
    DerivedPath as SerdeDerivedPath,
    KeyedBuildResult,
)
from ..serde.ids import LOCAL_STORE_ID
from .goal import ExecutionGoal
from .results import result_succeeded

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ..connection import ClientConn
    from .engine import GoalEngine
    from .ensure import EnsureDerivedPathGoal
    from .results import GoalResult


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
        # **In the order the client asked, and not sorted.** `nix build --json`
        # reads the answers by position, and `build.sh:8` of the functional
        # suite states which entry is which derivation. `Store::buildPaths` of
        # Nix keeps the order of the request. Issue #180.
        for serde_path in self.request.derived_paths:
            path = DerivedPath(str(serde_path))
            goal = await self.engine.get_ensure_derived_path_goal(path, self.request.build_mode, substituter_ids)
            await goal.subscribe(self.client)
            self._root_goals.append((SerdeDerivedPath(value=str(path)), goal))

        root_results = await self._run_the_root_goals([goal for _, goal in self._root_goals])
        # `for_the_wire`, because this is where a goal result becomes an answer
        # to a client. The goal system uses `BuildResultStatus.UNKNOWN`, which
        # is 102, and the wire carries the status as one byte that a client
        # looks up in a table of 15. Sending 102 makes `nix` raise "Invalid
        # BuildResult status f from remote" -- `f` and not 102, because the C++
        # format is `%d` against a `uint8_t` -- and drop the connection. A build
        # that merely could not be realised then reads as a broken daemon.
        #
        # **A goal that never ran carries no answer.**
        # `Worker::buildPathsWithResults` at `entry-points.cc:93` skips a goal
        # whose `exitCode` is still `ecBusy`, which is the goal that the first
        # failure stopped. The answer then holds fewer entries than the
        # request, and `build.sh:167` reads the count of `error:` lines.
        return BuildPathsWithResultsResponse(
            results=[
                KeyedBuildResult(path=serde_path, result=result.result.for_the_wire())
                for (serde_path, _goal), result in zip(self._root_goals, root_results, strict=True)
                if result is not None
            ]
        )

    async def _run_the_root_goals(self, goals: Sequence[EnsureDerivedPathGoal]) -> list[GoalResult | None]:
        """Run the goals of this request the way `Worker::run` of Nix runs them.

        **The first failure stops the request, unless the client set
        `keep-going`.** `Worker::removeGoal` at `worker.cc:173` clears
        `topGoals` when a top goal fails and `keepGoing` is off, and
        `Worker::run` then leaves its loop. A goal that had not started never
        starts, and it reports nothing.

        This starts no goal after the failure, and it cancels none. A build of
        pynixd survives the client that asked for it, which is the property
        that `AGENTS.md` states, so a cancellation here would kill work that
        another client still waits for.

        The answer holds `None` for each goal that never ran.

        **`max-jobs` limits the local builds alone, and `_build_slots` states
        when that reaches this loop.**
        """
        options = self.client.options if self.client is not None else None
        keep_going = bool(options.keep_going) if options is not None else False

        results: list[GoalResult | None] = [None] * len(goals)
        stop = anyio.Event()
        next_index = 0

        async def take_the_next_goal() -> None:
            nonlocal next_index
            while next_index < len(goals) and not stop.is_set():
                # The read and the increment take no await between them, so
                # two tasks cannot take the same goal.
                index = next_index
                next_index += 1
                result = await goals[index].result()
                results[index] = result
                if not keep_going and not result_succeeded(result.result):
                    stop.set()

        async with anyio.create_task_group() as tg:
            for _ in range(min(self._build_slots(), len(goals))):
                tg.start_soon(take_the_next_goal)

        return results

    def _build_slots(self) -> int:
        """How many goals of this request may run at one time.

        `max-jobs` of Nix limits the builds that the machine runs itself.
        `Worker::waitForBuildSlot` at `worker.cc:261` counts
        `getNrLocalBuilds()` against `settings.maxBuildJobs`, and
        `derivation-building-goal.cc:1182` asks the build hook to take the
        derivation when no local slot is free. A remote build therefore costs
        no slot, and `-j1` against a daemon that has builders still runs many
        builds at once.

        pynixd sends a build to a backend when it has one, so the limit reaches
        this loop only when every store is the local one. A client that asks
        for `-j1` against a pynixd with backends keeps the fan-out, which is
        the same answer that `nix-daemon` gives with `builders`.

        `main:build` of the functional suite reads this. It builds four
        fixed-output derivations that all give the wrong hash, with `-j1`, and
        it asserts one `error:` line. pynixd wrote five. Issue #190.
        """
        stores = self.engine.ctx.stores
        remote = [store_id for store_id in stores if store_id != LOCAL_STORE_ID]
        if remote:
            return len(self._root_goals) or 1
        options = self.client.options if self.client is not None else None
        if options is None:
            return len(self._root_goals) or 1
        # `max-jobs = 0` of Nix means "run no build here", and the goal system
        # of pynixd has no other place to run one. One slot at a time is the
        # nearest answer, and it is the answer that `nix-daemon` gives when the
        # machines file is empty as well: it raises rather than run nothing.
        return max(1, int(options.max_build_jobs))
