"""DerivedPath coordinator goal."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePath
from typing import TYPE_CHECKING

import structlog

from ..derived_path import DerivedPath, OutputsNames
from ..drv_hash import output_hashes
from ..drv_parser import ChildMapNode, to_basic_derivation
from ..serde import (
    BuildDerivationRequest,
    BuildResult,
    BuildResultStatus,
    DrvOutput,
    IsValidPathRequest,
    QueryRealisationRequest,
    Realisation,
    RegisterDrvOutputRequest,
    StorePath as SerdeStorePath,
)
from ..store_path import StorePath
from .dependencies import DependencyGroupGoal
from .goal import GoalHolder
from .resolution import _nix_drv_name, resolve_derivation, resolve_dynamic_derivation, unparse_basic_derivation
from .results import GoalResult, goal_failure, goal_success, result_succeeded

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from ..connection import ClientConn
    from ..drv_parser import Derivation
    from ..serde import BasicDerivation
    from .build_derivation import BuildDerivationGoal
    from .engine import GoalEngine
    from .results import DynamicPathMap

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
        store_path = StorePath(self.derived_path.drv_path)
        path = SerdeStorePath(path=self.derived_path.drv_path)
        response = await self.engine.ctx.local_store.execute(IsValidPathRequest(path=path))
        if response.valid:
            return self._opaque_success(store_path)

        # A path a backend built is available through pynixd, and it is not in
        # the local store. `NarFromPathHandler` and `DaemonProxy` both read it
        # from the backend that holds it, so a client that asks for it gets it.
        #
        # Only the local store was asked here until issue #160. A fleet build
        # therefore succeeded, recorded its outputs in `ctx.output_locations`,
        # and then failed the very next request for those outputs with "opaque
        # path is not valid locally". `nix copy` asks this way: it realises its
        # installables against the source store first, and a store path
        # installable becomes an opaque derived path.
        backend = self.engine.ctx.store_for_output_path(str(store_path))
        if backend is not None:
            log.debug(
                "opaque_path_resident_on_backend",
                path=str(store_path),
                store_id=backend.store_id,
            )
            return self._opaque_success(store_path)

        substitute = await self._try_substitute_path(store_path)
        if substitute is not None:
            return substitute
        return goal_failure(f"pynixd: opaque path is not valid locally: {self.derived_path}", BuildResultStatus.UNKNOWN)

    @staticmethod
    def _opaque_success(store_path: StorePath) -> GoalResult:
        """An opaque path needs no build, so the path itself is the one output."""
        return GoalResult(
            result=goal_success().result,
            resolved_outputs={"out": store_path},
            produced_paths={store_path},
        )

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
            substituted = await self._say_what_it_produced(substituted, parsed, selected_outputs)
            return substituted.with_dynamic_outputs(self.derived_path.base_store_path())

        realised = await self._already_realised(parsed, selected_outputs)
        if realised is not None:
            log.debug("ensure_derivation_already_realised", drv_path=str(drv_path), outputs=sorted(selected_outputs))
            return realised.with_dynamic_outputs(self.derived_path.base_store_path())

        child_results = await self._realise_input_derivations(parsed)

        store_path = Path(self.engine.ctx.local_store.store_path)
        dynamic_paths = {}
        for result in child_results:
            dynamic_paths.update(result.dynamic_paths)

        # **A derivation with an input derivation is resolved, and never
        # merely flattened.** `to_basic_derivation` moves the output path of
        # each input into `inputSrcs` and rewrites nothing else, so a
        # `DownstreamPlaceholder` in the build command stayed as it was and
        # the builder read it as a path. `Derivation::tryResolve` of Nix does
        # both, and this takes the same decision for every derivation rather
        # than for the kinds of output that a predicate lists. That predicate
        # was wrong twice: it missed a floating content-addressed output
        # (#183), and it missed a fixed-output derivation with a
        # content-addressed input, which carries a placeholder as well.
        domain_drv_path = StorePath(str(drv_path))
        resolved = True
        if parsed.dynamic_input_drvs and dynamic_paths:
            basic = resolve_dynamic_derivation(parsed, domain_drv_path, dynamic_paths)
        elif parsed.input_drvs:
            basic = resolve_derivation(parsed, domain_drv_path, await self._input_paths(parsed, dynamic_paths))
        else:
            basic = await to_basic_derivation(parsed, store_path)
            resolved = False

        # **Every output of the derivation goes on the wire, and the wanted
        # ones alone do not.** `BuildDerivation` carries no set of wanted
        # outputs, so the daemon builds each output that the derivation names.
        # It also rewrites `builtins.placeholder <name>` for each of those
        # names, at `derivation-builder.cc:802` of Nix. A derivation that lost
        # an output therefore reached the builder with the placeholder of that
        # output unrewritten, and the builder read a path that is not there.
        # The name of the derivation is also the dedup key of the build, so a
        # request for `drv^out` and a request for `drv^bin` made two builds of
        # one derivation. Issue #178.
        selected_paths = {name: path for name, path in basic.output_paths().items() if name in selected_outputs}
        substituted = await self._try_substitute_known_outputs(selected_paths)
        if substituted is not None:
            log.debug("ensure_derivation_substituted", drv_path=str(drv_path), outputs=sorted(selected_outputs))
            substituted = await self._say_what_it_produced(substituted, parsed, selected_outputs)
            return substituted.with_dynamic_outputs(self.derived_path.base_store_path())

        build_drv_path = await self._path_of_what_it_builds(basic, drv_path) if resolved else drv_path
        request = BuildDerivationRequest(
            drv_path=build_drv_path,
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
        result = result.with_dynamic_outputs(self.derived_path.base_store_path())
        result = await self._under_the_original_id(result, parsed)
        result.result = _only_wanted_outputs(result.result, selected_outputs)
        return result

    async def _already_realised(self, parsed: Derivation, wanted: set[str]) -> GoalResult | None:
        """The realisation of each wanted output, when the store holds one for all of them.

        **A derivation that names no output path is not therefore unbuilt.**
        A floating content-addressed output takes its path from what the build
        makes, so the derivation cannot name it, and a deferred output cannot
        name it either. The store answers instead: a realisation maps
        `DrvOutput{staticOutputHashes(drv)[name], name}` to the path.

        `DerivationGoal::checkPathValidity` at `derivation-goal.cc:405` reads
        it there, and a valid path makes the goal `AlreadyValid`. pynixd read
        the derivation alone, so it found no path and built the derivation
        again. `ca:build` sees it in `testGC`, which builds with `-j0` after a
        garbage collection and expects the rooted output to answer.

        Answers `None` when any wanted output names a path already, or when
        the store holds no realisation for one of them. The caller then goes
        on to the inputs and the build. Issue #185.
        """
        paths_of_drv = parsed.output_paths()
        if not wanted or any(str(paths_of_drv.get(name, "")) for name in wanted):
            return None
        hashes = await output_hashes(parsed, self.engine.ctx.local_store.read_derivation)
        if hashes is None:
            return None

        built: dict[str, Realisation] = {}
        resolved_outputs: dict[str, StorePath] = {}
        for output_name in sorted(wanted):
            digest = hashes.get(output_name)
            if digest is None:
                return None
            key = f"sha256:{digest}!{output_name}"
            response = await self.engine.ctx.local_store.execute(
                QueryRealisationRequest(drv_output=DrvOutput(key)),
            )
            realisation = next(iter(response.realisations), None)
            if realisation is None or realisation.out_path is None:
                return None
            path = StorePath(str(realisation.out_path))
            valid = await self.engine.ctx.local_store.execute(IsValidPathRequest(path=SerdeStorePath(path=str(path))))
            if not valid.valid:
                return None
            built[key] = realisation
            resolved_outputs[output_name] = path

        answer = goal_success()
        answer.resolved_outputs = resolved_outputs
        answer.produced_paths = set(resolved_outputs.values())
        answer.result = answer.result.model_copy(update={"built_outputs": built})
        return answer

    async def _under_the_original_id(self, result: GoalResult, parsed: Derivation) -> GoalResult:
        """Give each realisation the id that the original derivation makes, and register it.

        pynixd resolves a derivation before it sends it, so the daemon builds
        a different ATerm. `staticOutputHashes` of the daemon therefore answers
        a different hash, and the daemon registers each realisation under that
        hash. The client holds the original derivation and queries
        `DrvOutput{staticOutputHashes(original)[name], name}`, at
        `Store::queryPartialDerivationOutputMap` in `store-api.cc:406`. It
        found no realisation, and `nix-build.cc:730` and `built-path.cc:122`
        both stop the program with an assertion.

        Nix makes the same correction. `DerivationGoal` builds the resolved
        derivation and then re-registers each output under the hash of the
        original one, at `derivation-goal.cc:193-236`. The signatures go,
        because a signature covers the id. Issue #182.

        This goal makes the correction, and the build goal does not, because
        this goal is the one that holds the original derivation. Issue #184
        gave the build goal the path of the resolved derivation, so the build
        goal can no longer read the original one. A build goal is also shared
        between the clients that ask for it, and each one holds its own
        original derivation.
        """
        built = result.result.built_outputs
        if not built:
            return result

        hashes = await output_hashes(parsed, self.engine.ctx.local_store.read_derivation)
        answer: dict[str, Realisation] = {}
        changed = False
        for key, realisation in built.items():
            output_name = _realisation_output_name(key, realisation)
            digest = None if hashes is None else hashes.get(output_name)
            wanted = f"sha256:{digest}!{output_name}"
            if digest is None or wanted == key:
                answer[key] = realisation
                continue
            changed = True
            answer[wanted] = realisation.model_copy(update={"id": DrvOutput(wanted), "signatures": []})
            log.debug(
                "realisation_rekeyed",
                drv_path=self.derived_path.drv_path,
                output=output_name,
                sent=key,
                original=wanted,
            )

        if _needs_realisations(parsed):
            await self._register_realisations(answer.values())
        if changed:
            result.result = result.result.model_copy(update={"built_outputs": answer})
        return result

    async def _register_realisations(self, realisations: Iterable[Realisation]) -> None:
        """Put each realisation in the local store, under the id it now carries."""
        for realisation in realisations:
            if realisation.out_path is None:
                continue
            # `Realisation` carries the bare `<hash>-<name>`, which is
            # `StorePath::to_string` of Nix. `StorePath` of pynixd puts the
            # store directory in front of it again, and `IsValidPath` needs
            # the whole path.
            out_path = StorePath(str(realisation.out_path))
            valid = await self.engine.ctx.local_store.execute(
                IsValidPathRequest(path=SerdeStorePath(path=str(out_path))),
            )
            if not valid.valid:
                continue
            try:
                await self.engine.ctx.local_store.execute(RegisterDrvOutputRequest(realisation=realisation))
            except Exception:
                log.warning("register_drv_output_failed", drv_output=str(realisation.id), exc_info=True)

    async def _path_of_what_it_builds(
        self,
        basic: BasicDerivation,
        original: SerdeStorePath,
    ) -> SerdeStorePath:
        """Put the resolved derivation in the store, and answer its path.

        **The daemon reads the derivation on the disk, and not the one that
        `BuildDerivation` carries.** `queryPartialDerivationOutputMap` at
        `derivation-building-goal.cc:1239` takes the store copy whenever
        `drvPath` is valid there, and the client put the original derivation
        in the store when it instantiated it. So a resolved derivation sent
        under the original path answered every question about its outputs from
        the unresolved one: a deferred output had no path, the builder got a
        fallback scratch path, and the build failed with "failed to produce
        output path".

        Nix writes the resolved derivation to the store and builds that path,
        at `derivation-resolution-goal.cc`. This does the same. Issue #184.

        A store that cannot take a text file keeps the original path, which is
        what pynixd did before.
        """
        name = f"{_nix_drv_name(StorePath(str(original)))}.drv"
        text = unparse_basic_derivation(basic)
        references = {str(path) for path in basic.input_srcs}
        try:
            path = await self.engine.ctx.local_store.add_text_to_store(name, text, references)
        except Exception:
            log.warning("resolved_derivation_not_stored", drv_path=str(original), exc_info=True)
            return original
        log.debug("resolved_derivation_stored", original=str(original), resolved=path)
        return SerdeStorePath(path=path)

    async def _input_paths(self, parsed: Derivation, dynamic_paths: DynamicPathMap) -> DynamicPathMap:
        """Name the store path of each output of each input derivation.

        `resolve_derivation` needs one path for each of them, and a child goal
        gives a path only for the outputs that it built.
        `_input_outputs_requiring_goals` starts no goal for an output that is
        valid already, and a content-addressed input has no path in its own
        derivation. So neither source answers alone.

        A derivation that is not in the store answers with its own path, which
        is what `to_basic_derivation` did before this. That path is not the
        output, and no build works from it, but `inputSrcs` is not empty and
        the error that the daemon gives names the derivation. An output that
        the derivation reads but does not name gets nothing, and
        `resolve_derivation` then says which one.
        """
        answer: DynamicPathMap = {}
        for input_drv_path, output_names in parsed.input_drvs.items():
            drv_path = StorePath(input_drv_path)
            static: dict[str, StorePath] | None = None
            for output_name in output_names:
                key = (drv_path, output_name)
                built = dynamic_paths.get(key)
                if built is not None:
                    answer[key] = built
                    continue
                if static is None:
                    input_parsed = await self.engine.ctx.local_store.read_derivation(str(drv_path))
                    if input_parsed is None:
                        log.warning("input_derivation_not_found", drv_path=str(drv_path))
                        static = dict.fromkeys(output_names, drv_path)
                    else:
                        static = input_parsed.output_paths()
                path = static.get(output_name)
                if path:
                    answer[key] = path
        return answer

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

    async def _say_what_it_produced(
        self,
        result: GoalResult,
        parsed: Derivation,
        wanted: set[str],
    ) -> GoalResult:
        """Put a realisation for each wanted output in the answer.

        A status alone does not name a path.
        `DerivationBuildingGoal::checkPathValidity` of Nix answers with one
        realisation for each output it found, and a client reads the output
        paths out of them. pynixd answered an already-valid derived path with
        an empty map, so `nix build --json` wrote no `outputs` key at all:
        `BuiltPath::Built::toJSON` writes that key once for each output.
        Issue #179.

        **The realisation also goes into the store.**
        `DerivationGoal::checkPathValidity` at `derivation-goal.cc:445` does
        that: the output path is valid, and no realisation names it, so it
        writes one. A client then reads the path back through
        `queryPartialDerivationOutputMap`. `ca:build` needs it, at the second
        build of `dependentNonCA`: that build is a cut-off, so nothing is
        built and this answer is the whole answer. Issue #184.
        """
        if result.result.built_outputs:
            return result
        hashes = await output_hashes(parsed, self.engine.ctx.local_store.read_derivation)
        if hashes is None:
            return result

        built: dict[str, Realisation] = {}
        for output_name, path in result.resolved_outputs.items():
            digest = hashes.get(output_name)
            if output_name not in wanted or digest is None:
                continue
            # `sha256:<hex>!<name>` is `DrvOutput::to_string` of Nix, and the
            # bare `<hash>-<name>` is `StorePath::to_string`, which is what
            # `Realisation` carries in its JSON.
            key = f"sha256:{digest}!{output_name}"
            built[key] = Realisation(id=key, out_path=SerdeStorePath(path=PurePath(str(path)).name))
        if not built:
            return result

        if _needs_realisations(parsed):
            await self._register_realisations(built.values())

        answer = result.copy()
        answer.result = answer.result.model_copy(update={"built_outputs": built})
        return answer

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


def _needs_realisations(parsed: Derivation) -> bool:
    """True when the derivation names the path of no output, so a realisation says it.

    Nix registers a realisation for every output of every derivation while
    `ca-derivations` is on, at `derivation-builder.cc:1994` and again at
    `derivation-goal.cc:236`. It asks the setting, and pynixd cannot: a daemon
    with the feature off answers "experimental Nix feature 'ca-derivations' is
    disabled" to `RegisterDrvOutput`, and pynixd then discards a good
    connection as dirty.

    So this asks the derivation instead. An output with no path is the one
    case that needs the feature, and no such derivation exists while the
    feature is off. A floating content-addressed output names no path, and a
    deferred output names none either. An input-addressed output and a
    fixed-output one both name theirs.

    **The question is about the original derivation, and not the resolved
    one.** pynixd fills in a deferred output before it sends the derivation,
    so the resolved one names every path and answers no. The client holds the
    original, and `queryPartialDerivationOutputMap` at `store-api.cc:406`
    reads a realisation for each output that the original leaves open.
    `ca:build` builds `dependentNonCA`, which is that derivation.
    """
    return any(not output.path for output in parsed.outputs)


def _realisation_output_name(key: str, realisation) -> str:
    name = getattr(realisation.id, "output_name", "")
    return str(name) if name else key.split("!", 1)[-1]


def _only_wanted_outputs(result: BuildResult, wanted: set[str]) -> BuildResult:
    """Keep the outputs that the derived path names, and drop the others.

    The daemon builds every output of the derivation, so it answers with every
    output. A derived path names the outputs that the client wants, and
    `derivation-goal.cc:291` of Nix removes each other output from the answer.
    This makes the same removal, so `drv^out` answers with `out` alone.
    """
    built = result.built_outputs
    if not built:
        return result
    kept = {key: item for key, item in built.items() if _realisation_output_name(key, item) in wanted}
    if len(kept) == len(built):
        return result
    return result.model_copy(update={"built_outputs": kept})
