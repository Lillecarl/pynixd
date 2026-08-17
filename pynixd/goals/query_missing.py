"""Read-only QueryMissing planning goals."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import anyio
import structlog

from ..derived_path import DerivedPath
from ..serde import (
    IsValidPathRequest,
    LogNext,
    QueryMissingRequest,
    QueryMissingResponse,
    StorePath as SerdeStorePath,
)
from ..store_path import StorePath
from ..substitution_queue import SubstitutionAvailability
from .goal import ExecutionGoal
from .realisations import realisations_of

if TYPE_CHECKING:
    from anyio.abc import TaskGroup

    from ..drv_parser import ChildMapNode, Derivation
    from .engine import GoalEngine

log = structlog.get_logger(__name__)


@dataclass
class QueryMissingPlan:
    """Accumulated classification of paths into build/substitute/unknown buckets."""

    will_build: set[SerdeStorePath]
    will_substitute: set[SerdeStorePath]
    unknown: set[SerdeStorePath]
    download_size: int = 0
    nar_size: int = 0

    def add_substitute(self, path: StorePath, availability: SubstitutionAvailability) -> None:
        """Record that *path* can be substituted and accumulate its download size."""
        self.will_substitute.add(SerdeStorePath(path=str(path)))
        self.download_size += availability.download_size or 0
        self.nar_size += availability.nar_size or 0


@dataclass
class _Walk:
    """The state of one walk over the derived paths of a request.

    `Store::queryMissing` runs a thread pool over a work list, and each item
    may add more. This holds the same three things: the answer, the set of
    derived paths that the walk already read, and the group that runs a task
    for each new one.
    """

    plan: QueryMissingPlan
    task_group: TaskGroup
    seen: set[str] = field(default_factory=set)
    warnings: list[str] = field(default_factory=list)


@dataclass
class QueryMissingPlanGoal(ExecutionGoal[QueryMissingResponse]):
    """Read-only root goal for QueryMissing.

    `Store::queryMissing` at `misc.cc:102` is what this answers. It walks the
    input derivations of each derivation that must be built, and it walks the
    references of each path that a substituter holds.

    One part is missing: the substituters that the client names. pynixd reads
    the backends of its own configuration alone, so a path in a cache that
    `--substituters` names reads as `willBuild`. Issue #187.
    """

    engine: GoalEngine
    request: QueryMissingRequest

    def __post_init__(self) -> None:
        ExecutionGoal.__init__(self, self.engine)

    async def _run(self) -> QueryMissingResponse:
        plan = QueryMissingPlan(will_build=set(), will_substitute=set(), unknown=set())

        async with anyio.create_task_group() as tg:
            walk = _Walk(plan=plan, task_group=tg)
            for wire_path in self.request.derived_paths:
                self._enqueue(walk, wire_path.value)

        log.debug(
            "query_missing_goal_plan",
            requested=len(self.request.derived_paths),
            will_build=len(plan.will_build),
            will_substitute=len(plan.will_substitute),
            unknown=len(plan.unknown),
        )
        response = QueryMissingResponse(
            will_build=plan.will_build,
            will_substitute=plan.will_substitute,
            unknown=plan.unknown,
            download_size=plan.download_size,
            nar_size=plan.nar_size,
        )
        for text in walk.warnings:
            response.logs.add(LogNext(text=f"warning: {text}\n"))
        return response

    def _enqueue(self, walk: _Walk, wire_path: str) -> None:
        """Classify one derived path, unless the walk did that already.

        `doPath` of `Store::queryMissing` keeps a `done` set of the string form
        of each derived path, at `misc.cc:188`. A closure that reaches one
        derivation by two roads then reads it once.

        The check and the insertion take no await between them, so two tasks
        cannot both pass it.
        """
        if wire_path in walk.seen:
            return
        walk.seen.add(wire_path)
        walk.task_group.start_soon(self._classify_wire_path, wire_path, walk)

    async def _classify_wire_path(self, wire_path: str, walk: _Walk) -> None:
        derived_path = DerivedPath(wire_path)
        base_path = derived_path.base_store_path()
        # **The question is the shape of the derived path, and not the name of
        # the store path.** `Store::queryMissing` at `misc.cc:102` reads the
        # two cases of `DerivedPath` apart, and the opaque case asks about the
        # path alone. A `.drv` name in an opaque path means nothing there.
        if not derived_path.is_opaque:
            await self._classify_derivation(derived_path, walk)
            return

        await self._classify_opaque_path(base_path, walk.plan)

    def _must_build(self, drv_path: StorePath, parsed: Derivation | None, walk: _Walk) -> None:
        """The derivation builds, and so does each input that it needs.

        `mustBuildDrv` at `misc.cc:139` puts the derivation in `willBuild` and
        then enqueues each input derivation with the outputs that this one
        wants. pynixd classified the derived paths of the request alone, so
        `nix build` printed one derivation where `nix-daemon` printed the
        whole list. `impure-derivations.sh` is where that showed: the daemon
        named `impure.drv` and `impure-on-impure.drv`, and pynixd named the
        second one only.

        A dynamic input takes part as well. `enqueueDerivedPaths` at
        `misc.cc:130` walks the tree of one and enqueues a derived path for
        each level that names an output. `doPath` then warns for each of those
        and puts it in no bucket, so the answer holds nothing for one. The
        warning is the part that a reader sees, and pynixd wrote none.
        """
        walk.plan.will_build.add(SerdeStorePath(path=str(drv_path)))
        if parsed is None:
            return
        for input_drv_path, output_names in parsed.input_drvs.items():
            if not output_names:
                continue
            self._enqueue(walk, f"{input_drv_path}!{','.join(sorted(output_names))}")
        for input_drv_path, node in parsed.dynamic_input_drvs.items():
            self._enqueue_child_map(walk, str(input_drv_path), node)

    def _enqueue_child_map(self, walk: _Walk, prefix: str, node: ChildMapNode) -> None:
        """Enqueue one derived path for each level of a dynamic input.

        `enqueueDerivedPaths` at `misc.cc:130` takes the direct outputs of the
        level first, and then goes one level deeper for each child. The prefix
        grows by one output name at each step, which is
        `SingleDerivedPath::Built` of Nix.

        The separator is `!`, which is the one that `DerivedPath.__str__` and
        the rest of this walk use. Nix prints `^` and reads both.
        """
        if node.outputs:
            self._enqueue(walk, f"{prefix}!{','.join(sorted(node.outputs))}")
        for output_name, child in node.children.items():
            self._enqueue_child_map(walk, f"{prefix}!{output_name}", child)

    async def _classify_derivation(self, derived_path: DerivedPath, walk: _Walk) -> None:
        drv_path = derived_path.base_store_path()
        if derived_path.is_nested:
            # **A dynamic derived path goes in no bucket, and it gets a
            # warning.** `doPath` at `misc.cc:196` reads the derivation path of
            # the request, finds a `Built` and not an `Opaque`, and returns.
            # The subject of the warning is that inner path, which is this one
            # with the last output name removed.
            #
            # NIX-DEFECT (#191): Nix walks the whole request twice, so the
            # client reads this warning twice. `nix-build` calls
            # `queryMissing` to print the build plan, and `Worker::run` at
            # `worker.cc:340` calls it again inside the daemon. The second
            # walk answers the same question about the same paths, and the
            # daemon holds no result of the first one. pynixd walks once, so
            # it writes one warning, and issue #189 holds that difference.
            walk.warnings.append(
                f"Ignoring dynamic derivation {derived_path.outer.to_string()} "
                "while querying missing paths; not yet implemented",
            )
            return

        parsed = await self.engine.ctx.local_store.read_derivation(str(drv_path))
        if parsed is None:
            walk.plan.unknown.add(SerdeStorePath(path=str(drv_path)))
            return
        if parsed.is_dynamic:
            self._must_build(drv_path, parsed, walk)
            return

        output_paths = parsed.selected_output_paths(derived_path.output_names)
        if not output_paths:
            self._must_build(drv_path, parsed, walk)
            return

        unnamed = [name for name, path in output_paths.items() if not str(path)]
        realised = await self._realised_paths(parsed, unnamed) if unnamed else {}
        if realised is None:
            self._must_build(drv_path, parsed, walk)
            return

        needs_build = False
        for output_name, output_path in output_paths.items():
            path = output_path if str(output_path) else realised[output_name]
            if not await self._classify_output_path(path, walk.plan):
                needs_build = True
        if needs_build:
            self._must_build(drv_path, parsed, walk)

    async def _realised_paths(self, parsed: Derivation, wanted: list[str]) -> dict[str, StorePath] | None:
        """The path of each output that the derivation does not name.

        `Store::queryMissing` reads `queryPartialDerivationOutputMap` at
        `misc.cc:217`, which answers the path of a content-addressed output
        from the realisation that the build registered. When every wanted
        output has a path, and every one of those paths is valid, the loop at
        `misc.cc:225` returns and the derivation is in no bucket at all.

        pynixd read the derivation alone. A content-addressed derivation names
        no output path, so pynixd answered `willBuild` for one that it had
        already built, and the client then took a different code path from the
        one it takes with `nix-daemon`. Issue #175.

        Answers `None` when a wanted output has no realisation, and also for
        an impure derivation, which Nix never treats as built. That is the
        `knownOutputPaths = false` of Nix, and the derivation must be built.
        """
        if parsed.is_impure:
            return None
        found = await realisations_of(parsed, wanted, self.engine.ctx.local_store)
        if found is None:
            return None
        return {key.rpartition("!")[2]: StorePath(str(realisation.out_path)) for key, realisation in found.items()}

    async def _classify_opaque_path(self, path: StorePath, plan: QueryMissingPlan) -> None:
        if not await self._classify_output_path(path, plan):
            plan.unknown.add(SerdeStorePath(path=str(path)))

    async def _classify_output_path(self, path: StorePath, plan: QueryMissingPlan) -> bool:
        response = await self.engine.ctx.local_store.execute(IsValidPathRequest(path=SerdeStorePath(path=str(path))))
        if response.valid:
            return True
        availability = await self._can_substitute(path)
        if availability.available:
            plan.add_substitute(path, availability)
            return True
        return False

    async def _can_substitute(self, path: StorePath) -> SubstitutionAvailability:
        scheduler = self.engine.ctx.scheduler
        if scheduler is None:
            return SubstitutionAvailability.unavailable()
        return await scheduler.substitution_queue.can_substitute(path)
