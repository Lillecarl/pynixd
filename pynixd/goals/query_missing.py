"""Read-only QueryMissing planning goals."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import anyio
import structlog

from ..derived_path import DerivedPath
from ..serde import IsValidPathRequest, QueryMissingRequest, QueryMissingResponse, StorePath as SerdeStorePath
from ..store_path import StorePath
from ..substitution_queue import SubstitutionAvailability
from .goal import ExecutionGoal
from .realisations import realisations_of

if TYPE_CHECKING:
    from ..drv_parser import Derivation
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
class QueryMissingPlanGoal(ExecutionGoal[QueryMissingResponse]):
    """Read-only root goal for QueryMissing.

    This preserves the current limited classification behavior while moving it
    behind the goal-system read-only entrypoint. Later slices should teach this
    planner about substituter availability and dependency walking.
    """

    engine: GoalEngine
    request: QueryMissingRequest

    def __post_init__(self) -> None:
        ExecutionGoal.__init__(self, self.engine)

    async def _run(self) -> QueryMissingResponse:
        plan = QueryMissingPlan(will_build=set(), will_substitute=set(), unknown=set())

        async with anyio.create_task_group() as tg:
            for wire_path in self.request.derived_paths:
                tg.start_soon(self._classify_wire_path, wire_path.value, plan)

        log.debug(
            "query_missing_goal_plan",
            requested=len(self.request.derived_paths),
            will_build=len(plan.will_build),
            will_substitute=len(plan.will_substitute),
            unknown=len(plan.unknown),
        )
        return QueryMissingResponse(
            will_build=plan.will_build,
            will_substitute=plan.will_substitute,
            unknown=plan.unknown,
            download_size=plan.download_size,
            nar_size=plan.nar_size,
        )

    async def _classify_wire_path(self, wire_path: str, plan: QueryMissingPlan) -> None:
        derived_path = DerivedPath(wire_path)
        base_path = derived_path.base_store_path()
        if base_path.is_derivation():
            await self._classify_derivation(derived_path, plan)
            return

        await self._classify_opaque_path(base_path, plan)

    async def _classify_derivation(self, derived_path: DerivedPath, plan: QueryMissingPlan) -> None:
        drv_path = derived_path.base_store_path()
        if derived_path.is_nested:
            plan.will_build.add(SerdeStorePath(path=str(drv_path)))
            return

        parsed = await self.engine.ctx.local_store.read_derivation(str(drv_path))
        if parsed is None:
            plan.unknown.add(SerdeStorePath(path=str(drv_path)))
            return
        if parsed.is_dynamic:
            plan.will_build.add(SerdeStorePath(path=str(drv_path)))
            return

        output_paths = parsed.selected_output_paths(derived_path.output_names)
        if not output_paths:
            plan.will_build.add(SerdeStorePath(path=str(drv_path)))
            return

        unnamed = [name for name, path in output_paths.items() if not str(path)]
        realised = await self._realised_paths(parsed, unnamed) if unnamed else {}
        if realised is None:
            plan.will_build.add(SerdeStorePath(path=str(drv_path)))
            return

        needs_build = False
        for output_name, output_path in output_paths.items():
            path = output_path if str(output_path) else realised[output_name]
            if not await self._classify_output_path(path, plan):
                needs_build = True
        if needs_build:
            plan.will_build.add(SerdeStorePath(path=str(drv_path)))

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
