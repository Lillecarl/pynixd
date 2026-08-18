"""Request-root goals for daemon build operations."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import anyio
import structlog

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

log = structlog.get_logger(__name__)


async def _the_result_unless_it_stops(goal: EnsureDerivedPathGoal, stop: anyio.Event) -> GoalResult | None:
    """The result of *goal*, or `None` when *stop* comes first.

    **A goal that the request left behind reports nothing, and it keeps
    running.** Nix takes both halves of that. `Worker::run` leaves its loop
    when `topGoals` is empty, so a goal that is still building never reaches
    `amDone`, its `exitCode` stays `ecBusy`, and
    `Worker::buildPathsWithResults` at `entry-points.cc:93` skips it. Nix then
    destroys the goal, which kills the builder as well.

    pynixd keeps the build. A build of pynixd serves every client that asked
    for the same derivation, and one client that gave up must not take the
    work of the others. So this stops waiting and leaves the build alone.

    `main:build` measured what the waiting costs. `nix flake check
    ./cancelled-builds -j2` builds `fast-fail` and `slow` together, and
    `slow` blocks on a fifo. Nix answers as soon as `fast-fail` fails and
    says nothing about `slow`; `build.sh:245` asserts that no `error:` line
    names it. pynixd waited for `slow` and then reported it, and with a fifo
    that nothing opens the wait has no end.

    `asyncio.shield` is the one primitive that does this. `Goal.result`
    awaits the `asyncio.Task` of the goal, and a cancellation of the awaiting
    task cancels that task as well, because `Task.__step` cancels the future
    it waits on. `shield` puts a second future between the two, and a
    cancellation reaches that one alone. It also reads the outcome of the
    goal when nothing else does, so an abandoned failure raises no "exception
    was never retrieved" report. anyio offers no equivalent, and the goal
    system is asyncio below `Goal.result`. Issue #196.
    """
    if stop.is_set():
        return None
    shielded = asyncio.shield(goal.result())
    result: GoalResult | None = None

    async def watch_the_stop() -> None:
        await stop.wait()
        tg.cancel_scope.cancel()

    async def take_the_result() -> None:
        nonlocal result
        result = await shielded
        tg.cancel_scope.cancel()

    async with anyio.create_task_group() as tg:
        tg.start_soon(watch_the_stop)
        tg.start_soon(take_the_result)

    return result


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
        #
        # **`for_the_wire` also takes the feature set of this client.** A proxy
        # reads a result on one connection and writes it on another, and the
        # two negotiate apart. A backend that offers
        # `realisation-with-path-not-hash` fills one `builtOutputs` field and
        # leaves the other at `None`, and a client that offers nothing reads
        # the one that is `None`. Issue #162.
        client_features = self.client.standard_features if self.client is not None else frozenset()
        return BuildPathsWithResultsResponse(
            results=[
                KeyedBuildResult(path=serde_path, result=result.result.for_the_wire(client_features))
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

        **The order of the request is not the order of the work.**
        `_goal_order` states which goal runs first, and the answer stays in
        the order the client asked.
        """
        options = self.client.options if self.client is not None else None
        keep_going = bool(options.keep_going) if options is not None else False

        results: list[GoalResult | None] = [None] * len(goals)
        stop = anyio.Event()
        order = self._goal_order(goals)
        next_position = 0

        async def take_the_next_goal() -> None:
            nonlocal next_position
            while next_position < len(order) and not stop.is_set():
                # The read and the increment take no await between them, so
                # two tasks cannot take the same goal.
                index = order[next_position]
                next_position += 1
                log.debug("root_goal_taken", index=index, derived_path=str(goals[index].derived_path))
                result = await _the_result_unless_it_stops(goals[index], stop)
                if result is None:
                    log.debug("root_goal_abandoned", index=index, derived_path=str(goals[index].derived_path))
                    # **A goal the request left behind must go quiet.** It
                    # keeps building, because a build of pynixd serves every
                    # client that asked for it, and this client has had its
                    # answer. Without the unsubscribe its log still reaches
                    # that client, and `build.sh:167` counts the `error:`
                    # lines of the whole run. Issue #196.
                    await goals[index].unsubscribe(self.client)
                    return
                if self._another_root_carries_this_failure(result, index, goals):
                    log.debug(
                        "root_goal_answered_by_another_root",
                        index=index,
                        derived_path=str(goals[index].derived_path),
                        failing_derivation=str(result.failing_derivation),
                    )
                    continue
                results[index] = result
                if not keep_going and not result_succeeded(result.result):
                    stop.set()

        slots = self._build_slots()
        log.debug(
            "root_goal_slots",
            slots=slots,
            goals=len(goals),
            keep_going=keep_going,
            max_build_jobs=None if options is None else int(options.max_build_jobs),
            stores=sorted(self.engine.ctx.stores),
        )
        async with anyio.create_task_group() as tg:
            for _ in range(min(slots, len(goals))):
                tg.start_soon(take_the_next_goal)

        return results

    @staticmethod
    def _another_root_carries_this_failure(
        result: GoalResult,
        index: int,
        goals: Sequence[EnsureDerivedPathGoal],
    ) -> bool:
        """Is this failure the failure of another root of the same request?

        **A request answers for the build that failed, and not for what waited
        for it.** `nix build fast-fail^out depends-on-fail^out` names both, and
        the second one has the first as an input. Nix answers with the failure
        of `fast-fail` alone. `Worker::removeGoal` at `worker.cc:173` clears
        `topGoals` as soon as one top goal fails and `keep-going` is off, so
        the goal of `depends-on-fail` never reaches `amDone`, its `exitCode`
        stays `ecBusy`, and `entry-points.cc:93` skips it.

        pynixd answered for both, and `build.sh:279` reads the difference: the
        client wrote one `error:` block more than the control run, because the
        answer held a `DEPENDENCY_FAILED` result that Nix does not send.

        pynixd cannot read `topGoals` for this, because it runs its root goals
        together and each one answers on its own. `failing_derivation` is the
        equivalent question, and it is a better one: it names the build that
        really failed, so a chain of any depth points at the same derivation.
        A root that names another root of this request learned nothing that
        the other root does not already say.

        The flake-check half of the same test needs no rule, and shows why the
        name is what decides. It asks for `^*` and not `^out`, so the root and
        the input are two goal objects for one derivation, and the failure of
        each one names itself. Issue #196.
        """
        failing = result.failing_derivation
        if failing is None or result_succeeded(result.result):
            return False
        if failing == goals[index].derived_path.base_store_path():
            return False
        return any(
            failing == goal.derived_path.base_store_path() for position, goal in enumerate(goals) if position != index
        )

    @staticmethod
    def _goal_order(goals: Sequence[EnsureDerivedPathGoal]) -> list[int]:
        """The positions of *goals*, in the order that Nix takes its goals.

        **Nix takes a derivation by its name, and not by its place in the
        request.** `Worker::awake` is a `std::set` over `CompareGoalPtrs`,
        which reads `Goal::key()`, and `DerivationBuildingGoal::key()` at
        `derivation-building-goal.cc:54` builds `"dd$" + name + "$" + path`.
        The name comes before the path, so `aardvark` runs before `baboon`
        whatever order the client wrote. `goal.hh:604` states the rule.

        `main:build` measured the difference. It builds x1, x2, x3 and x4 with
        `-j1`, all four give a hash mismatch, and it asserts that the one
        `error:` line names **x1**. The client sends the four in store-path
        order, and `f71q...-x3.drv` sorts first, so pynixd built x3 and the
        test read a mismatch of the wrong derivation.

        The answer of the request keeps the order the client asked. This
        decides which goal runs first, and `results[index]` still writes to
        the place of the request. Issue #196.
        """
        return sorted(
            range(len(goals)),
            key=lambda index: (
                goals[index].derived_path.base_store_path().base_name(),
                str(goals[index].derived_path),
            ),
        )

    def _build_slots(self) -> int:
        """How many root goals of this request run at one time.

        `max-jobs` of Nix limits the builds that the machine runs itself.
        `Worker::waitForBuildSlot` at `worker.cc:261` counts
        `getNrLocalBuilds()` against `settings.maxBuildJobs`, and a remote
        build costs no slot.

        **A substituter is not a builder.** `no_schedule` marks a store that
        the scheduler never sends a build to. This counted every store that
        is not the local one, so one `http-cache.nixos.org` lifted the limit
        for every request. Issues #190 and #196.

        **This limits goals, and Nix limits builds. That difference is the
        last regression of #196, and closing it needs more than this line.**
        `main:build` reads it at `build.sh:247`: with `-j2` pynixd takes the
        first two goals by name, which are `depends-on-fail` and
        `depends-on-slow`, so `fast-fail` is never a root of its own and the
        answer names the wrong one.

        Starting every goal here does fix that assertion, and it hangs the
        `nix build` half of the same fixture at `build.sh:269` -- measured, a
        300 s timeout. A hang is worse than a wrong answer, so the change is
        not here. The build queue hands builds out in derivation-name order
        now, which is one of the two pieces that a correction needs; the
        other is dropping a queued build that no goal wants.
        """
        stores = self.engine.ctx.stores
        builders = [
            store_id for store_id, store in stores.items() if store_id != LOCAL_STORE_ID and not store.no_schedule
        ]
        if builders:
            return len(self._root_goals) or 1
        options = self.client.options if self.client is not None else None
        if options is None:
            return len(self._root_goals) or 1
        return max(1, int(options.max_build_jobs))
