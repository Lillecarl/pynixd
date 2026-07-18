"""DerivedPath coordinator goal."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from ..derived_path import DerivedPath, OutputsAll, OutputsNames
from ..drv_parser import ChildMapNode, to_basic_derivation
from ..serde import BuildDerivationRequest, BuildResultStatus, IsValidPathRequest
from ..serde import StorePath as SerdeStorePath
from ..store_path import StorePath
from .dependencies import DependencyGroupGoal
from .goal import GoalHolder
from .resolution import resolve_derivation, resolve_dynamic_derivation
from .results import GoalResult, goal_failure, goal_success, result_succeeded

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ..connection import ClientConn
    from .build_derivation import BuildDerivationGoal
    from .engine import GoalEngine

log = structlog.get_logger(__name__)


@dataclass
class EnsureDerivedPathGoal(GoalHolder[GoalResult]):
    """Coordinate the production of a derived path via substitution or build."""

    engine: GoalEngine
    derived_path: DerivedPath
    build_mode: int
    substituter_ids: tuple[str, ...]
    _subscribers: list[ClientConn] = field(default_factory=list)
    _build_goal: BuildDerivationGoal | None = None

    def __post_init__(self) -> None:
        """Initialize the GoalHolder base with the shared engine."""
        GoalHolder.__init__(self, self.engine)

    async def subscribe(self, client: ClientConn | None) -> None:
        """Register a client for real-time log forwarding from the underlying build goal."""
        if client is None:
            return
        async with self._lock:
            build_goal = self._build_goal
            if build_goal is None:
                self._subscribers.append(client)
                return
        await build_goal.subscribe(client)

    async def _run(self) -> GoalResult:
        if self.derived_path.is_opaque:
            return await self._ensure_opaque()
        if self.derived_path.is_nested:
            return await self._ensure_nested()
        return await self._ensure_flat_derivation()

    async def _ensure_opaque(self) -> GoalResult:
        path = SerdeStorePath(path=self.derived_path.drv_path)
        response = await self.engine.ctx.local_store.execute(IsValidPathRequest(path=path))
        if response.valid:
            store_path = StorePath(self.derived_path.drv_path)
            return GoalResult(
                result=goal_success().result,
                resolved_outputs={"out": store_path},
                produced_paths={store_path},
            )
        substitute = await self._try_substitute_path(StorePath(self.derived_path.drv_path))
        if substitute is not None:
            return substitute
        return goal_failure(f"pynixd: opaque path is not valid locally: {self.derived_path}", BuildResultStatus.UNKNOWN)

    async def _ensure_nested(self) -> GoalResult:
        outer_goal = await self.engine.get_ensure_derived_path_goal(
            self.derived_path.outer,
            self.build_mode,
            self.substituter_ids,
        )
        await outer_goal.subscribe_many(self._subscribers)
        outer_result = await self.run_child(outer_goal)

        chain_output = self.derived_path.chain[-1]
        inner_drv = outer_result.resolved_outputs.get(chain_output)
        if inner_drv is None:
            return goal_failure(
                f"pynixd: nested derived path did not produce {chain_output}: {self.derived_path}",
                BuildResultStatus.UNKNOWN,
            )
        if not inner_drv.is_derivation():
            return outer_result

        wrapped = self.derived_path.wrap(inner_drv)
        remainder_goal = await self.engine.get_ensure_derived_path_goal(wrapped, self.build_mode, self.substituter_ids)
        await remainder_goal.subscribe_many(self._subscribers)
        result = await self.run_child(remainder_goal)

        nested_result = result.with_dynamic_outputs(self.derived_path.base_store_path())
        nested_result.produced_paths.add(inner_drv)
        return nested_result

    async def _ensure_flat_derivation(self) -> GoalResult:
        drv_path = SerdeStorePath(path=self.derived_path.drv_path)
        parsed = await self.engine.ctx.local_store.read_derivation(str(drv_path))
        if parsed is None:
            return goal_failure(f"pynixd: derivation not found: {drv_path}", BuildResultStatus.UNKNOWN)

        requested_outputs = self.derived_path.output_names
        if requested_outputs == {"*"}:
            selected_outputs = {output.name for output in parsed.outputs}
        else:
            selected_outputs = requested_outputs

        known_outputs = {output.name for output in parsed.outputs}
        missing_outputs = selected_outputs - known_outputs
        if missing_outputs:
            return goal_failure(
                f"pynixd: derivation {drv_path} does not define outputs: {', '.join(sorted(missing_outputs))}",
                BuildResultStatus.UNKNOWN,
            )

        early_outputs = {name: path for name, path in parsed.output_paths().items() if name in selected_outputs}
        substituted = await self._try_substitute_known_outputs(early_outputs)
        if substituted is not None:
            log.debug("ensure_derivation_substituted", drv_path=str(drv_path), outputs=sorted(selected_outputs))
            return substituted.with_dynamic_outputs(self.derived_path.base_store_path())

        child_results = await self._realise_input_derivations(parsed)

        store_path = Path(self.engine.ctx.local_store.store_path)
        dynamic_paths = {}
        for result in child_results:
            dynamic_paths.update(result.dynamic_paths)

        domain_drv_path = StorePath(str(drv_path))
        needs_resolution = _needs_placeholder_resolution(parsed)
        if parsed.dynamic_input_drvs and dynamic_paths:
            basic = resolve_dynamic_derivation(parsed, domain_drv_path, dynamic_paths)
        elif dynamic_paths and needs_resolution:
            basic = resolve_derivation(parsed, domain_drv_path, dynamic_paths)
        else:
            basic = await to_basic_derivation(parsed, store_path)

        if not isinstance(self.derived_path.outputs, OutputsAll):
            basic.outputs = {name: output for name, output in basic.outputs.items() if name in selected_outputs}

        substituted = await self._try_substitute_known_outputs(basic.output_paths())
        if substituted is not None:
            log.debug("ensure_derivation_substituted", drv_path=str(drv_path), outputs=sorted(selected_outputs))
            return substituted.with_dynamic_outputs(self.derived_path.base_store_path())

        request = BuildDerivationRequest(
            drv_path=drv_path,
            derivation=basic,
            build_mode=self.build_mode,
        )
        build_goal = await self.engine.get_build_derivation_goal(request)
        async with self._lock:
            self._build_goal = build_goal
            subscribers = list(self._subscribers)
            self._subscribers.clear()
        for client in subscribers:
            await build_goal.subscribe(client)
        result = await self.run_child(build_goal)
        return result.with_dynamic_outputs(self.derived_path.base_store_path())

    async def _realise_input_derivations(self, parsed) -> list[GoalResult]:
        child_goals: list[EnsureDerivedPathGoal] = []
        for input_drv_path, output_names in parsed.input_drvs.items():
            needed_outputs = await self._input_outputs_requiring_goals(
                StorePath(input_drv_path),
                output_names,
            )
            child_goals.extend(
                [
                    await self._child_goal(StorePath(input_drv_path), output_name)
                    for output_name in output_names
                    if output_name in needed_outputs
                ]
            )

        child_goals.extend(
            [
                await self.engine.get_ensure_derived_path_goal(child_dp, self.build_mode, self.substituter_ids)
                for input_drv_path, node in parsed.dynamic_input_drvs.items()
                for child_dp in _child_map_to_derived_paths(StorePath(input_drv_path), node)
            ]
        )

        if not child_goals:
            return []
        return await self.run_child(DependencyGroupGoal(self.engine, child_goals))

    async def _input_outputs_requiring_goals(
        self,
        drv_path: StorePath,
        output_names: list[str],
    ) -> set[str]:
        parsed = await self.engine.ctx.local_store.read_derivation(str(drv_path))
        if parsed is None:
            return set(output_names)
        if parsed.builder.startswith("builtin:"):
            return set()
        paths = parsed.output_paths()
        needed: set[str] = set()
        for output_name in output_names:
            output_path = paths.get(output_name)
            if output_path is None:
                needed.add(output_name)
                continue
            response = await self.engine.ctx.local_store.execute(
                IsValidPathRequest(path=SerdeStorePath(path=str(output_path))),
            )
            if not response.valid:
                needed.add(output_name)
        return needed

    async def _child_goal(self, drv_path: StorePath, output_name: str) -> EnsureDerivedPathGoal:
        dp = DerivedPath._from_components(
            drv_path=drv_path,
            chain=(),
            outputs=OutputsNames(frozenset({output_name})),
        )
        return await self.engine.get_ensure_derived_path_goal(dp, self.build_mode, self.substituter_ids)

    async def _try_substitute_known_outputs(self, output_paths: Mapping[str, StorePath | None]) -> GoalResult | None:
        selected = {name: path for name, path in output_paths.items() if path is not None}
        if not selected:
            return None

        results: dict[str, GoalResult] = {}
        for output_name, path in selected.items():
            response = await self.engine.ctx.local_store.execute(
                IsValidPathRequest(path=SerdeStorePath(path=str(path)))
            )
            if response.valid:
                results[output_name] = GoalResult(
                    result=goal_success().result,
                    resolved_outputs={output_name: path},
                    produced_paths={path},
                )
                continue
            substituted = await self._try_substitute_path(path)
            if substituted is None:
                return None
            if not result_succeeded(substituted.result):
                return substituted
            results[output_name] = substituted.with_single_output(output_name, path)

        merged = goal_success()
        for output_name, result in results.items():
            merged.resolved_outputs[output_name] = selected[output_name]
            merged.produced_paths.update(result.produced_paths)
        return merged

    async def _try_substitute_path(self, path: StorePath) -> GoalResult | None:
        substitute_goal = await self.engine.get_substitute_path_goal(path, self.substituter_ids)
        attempt = await self.run_child(substitute_goal)
        if not attempt.found:
            return None
        return attempt.result

    async def subscribe_many(self, clients: list[ClientConn]) -> None:
        for client in clients:
            await self.subscribe(client)


def _child_map_to_derived_paths(drv_path: StorePath, node: ChildMapNode) -> list[DerivedPath]:
    results: list[DerivedPath] = []

    def walk(current: ChildMapNode, chain: tuple[str, ...]) -> None:
        for child_name, child_node in current.children.items():
            walk(child_node, (*chain, child_name))
        results.extend(
            [
                DerivedPath._from_components(
                    drv_path=drv_path,
                    chain=chain,
                    outputs=OutputsNames(frozenset({output_name})),
                )
                for output_name in current.outputs
            ]
        )

    walk(node, ())
    return results


def _needs_placeholder_resolution(parsed) -> bool:
    if parsed.dynamic_input_drvs:
        return True
    return any(output.path == "" and output.hash_algo == "" and output.hash_value == "" for output in parsed.outputs)
